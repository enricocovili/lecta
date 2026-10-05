// The AI conversation of a course (or of one of its lessons' labs): sessions, sending, live streaming of a turn (with reconnect) and undo.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, del, get, post, streamSSE } from "../../lib/api";
import { toast, toastError } from "../ui";
import type { ChangedFile, Message, Scope, SessionInfo, Step } from "./types";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function normalize(m: Partial<Message> & { id: number; role: Message["role"] }): Message {
  return {
    status: "done",
    content: "",
    scope: {},
    steps: [],
    change: null,
    suggestions: [],
    review: null,
    error: null,
    ...m,
  } as Message;
}

/** Chapters the AI changed in the last day (marked in the outline). */
export function recentAiChapters(messages: Message[], hours = 24): Set<number> {
  const out = new Set<number>();
  const since = Date.now() - hours * 3600_000;
  for (const m of messages) {
    if (m.role !== "assistant" || m.change?.status !== "applied") continue;
    if (m.created_at && new Date(m.created_at).getTime() < since) continue;
    for (const f of m.change.files) if (f.chapter_id != null) out.add(f.chapter_id);
    for (const c of m.change.chapters ?? []) if (c.op !== "deleted") out.add(c.id);
  }
  return out;
}

interface Opts {
  courseId: number;
  /** the conversations of this lab instead of the course's text */
  labId?: number;
  /** The AI wrote something (`final`: the turn is over or was undone). Refresh the draft. */
  onChange: (files: ChangedFile[], final: boolean) => void;
}

export function useAssistant({ courseId, labId, onChange }: Opts) {
  const where = labId ? { course_id: courseId, lab_id: labId } : { course_id: courseId };
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [streamingId, setStreamingId] = useState<number | null>(null);
  const abort = useRef<AbortController | null>(null);
  const sidRef = useRef<number | null>(null);
  const cb = useRef(onChange);
  cb.current = onChange;
  const msgRef = useRef<Message[]>([]);
  msgRef.current = messages;

  const patch = useCallback((id: number, fn: (m: Message) => Message) => setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m))), []);

  const detach = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    setStreamingId(null);
  }, []);

  /** Follow a reply's events until its final one; reconnects (from where it left off) if the connection drops. */
  const attach = useCallback(
    async (replyId: number, sid: number) => {
      abort.current?.abort();
      const ac = new AbortController();
      abort.current = ac;
      setStreamingId(replyId);
      let last: string | null = null;
      let finished = false;
      let attempts = 0;

      const handle = (ev: string, d: any) => {
        if (ev === "text") patch(replyId, (m) => ({ ...m, content: m.content + String(d?.delta ?? "") }));
        else if (ev === "tool")
          patch(replyId, (m) => {
            const step: Step = { id: String(d.id), name: d.name, label: d.label, status: d.status ?? "running" };
            const has = m.steps.some((s) => s.id === step.id);
            return { ...m, steps: has ? m.steps.map((s) => (s.id === step.id ? { ...s, ...step } : s)) : [...m.steps, step] };
          });
        else if (ev === "tool_done")
          patch(replyId, (m) => ({ ...m, steps: m.steps.map((s) => (s.id === String(d.id) ? { ...s, status: d.ok ? "ok" : "error", summary: d.summary } : s)) }));
        else if (ev === "change") {
          const files: ChangedFile[] = d?.files ?? [];
          patch(replyId, (m) => ({ ...m, change: { status: "applied", files, chapters: m.change?.chapters } }));
          cb.current(files, false);
        } else if (ev === "done") {
          finished = true;
          const reply = d?.reply ? normalize(d.reply) : null;
          if (reply) patch(replyId, (m) => ({ ...reply, created_at: reply.created_at ?? m.created_at }));
          else patch(replyId, (m) => ({ ...m, status: "done" }));
          cb.current(reply?.change?.files ?? [], true);
        } else if (ev === "error") {
          finished = true;
          patch(replyId, (m) => ({ ...m, status: "error", error: String(d?.message ?? "Errore dell'assistente") }));
          cb.current([], true);
        }
      };

      while (!ac.signal.aborted && !finished) {
        // The server replays from the first event (or from `?after=`): start over instead of duplicating text.
        if (last === null) patch(replyId, (m) => ({ ...m, content: "", steps: [], change: null }));
        try {
          await streamSSE(
            `/api/chat/replies/${replyId}/events${last ? `?after=${encodeURIComponent(last)}` : ""}`,
            (ev, d, id) => {
              if (id) last = id;
              attempts = 0;
              handle(ev, d);
            },
            ac.signal,
          );
        } catch (e) {
          if (ac.signal.aborted) break;
          if (e instanceof ApiError && [401, 403, 404].includes(e.status)) break;
        }
        if (finished || ac.signal.aborted) break;
        // The stream closed without a final event: is the turn over on the server?
        attempts++;
        if (attempts > 25) break;
        try {
          const s = await get<{ messages: Message[] }>(`/api/chat/sessions/${sid}`);
          const r = s.messages.find((m) => m.id === replyId);
          if (r && r.status !== "streaming") {
            patch(replyId, () => normalize(r));
            cb.current(r.change?.files ?? [], true);
            finished = true;
            break;
          }
        } catch {
          /* offline: keep trying */
        }
        await sleep(Math.min(700 * attempts, 4000));
      }
      if (abort.current === ac) {
        abort.current = null;
        setStreamingId(null);
      }
    },
    [patch],
  );

  const loadSession = useCallback(
    async (sid: number) => {
      detach();
      sidRef.current = sid;
      setSessionId(sid);
      setLoading(true);
      try {
        const s = await get<{ id: number; messages: Message[] }>(`/api/chat/sessions/${sid}`);
        if (sidRef.current !== sid) return;
        const ms = (s.messages ?? []).map(normalize);
        setMessages(ms);
        const live = [...ms].reverse().find((m) => m.role === "assistant" && m.status === "streaming");
        if (live) void attach(live.id, sid);
      } catch (e) {
        toastError(e);
      } finally {
        setLoading(false);
      }
    },
    [attach, detach],
  );

  // The course's conversations: open the most recent one.
  useEffect(() => {
    let stop = false;
    setLoading(true);
    get<SessionInfo[]>(`/api/chat/sessions?course_id=${courseId}${labId ? `&lab_id=${labId}` : ""}`)
      .then(async (list) => {
        if (stop) return;
        const sorted = [...list].sort((a, b) => b.id - a.id);
        setSessions(sorted);
        if (sorted[0]) await loadSession(sorted[0].id);
        else setLoading(false);
      })
      .catch((e) => {
        if (!stop) {
          toastError(e);
          setLoading(false);
        }
      });
    return () => {
      stop = true;
      detach();
    };
  }, [courseId, labId, loadSession, detach]);

  const ensureSession = async (): Promise<number> => {
    if (sidRef.current) return sidRef.current;
    const s = await post<SessionInfo>("/api/chat/sessions", where);
    setSessions((xs) => [s, ...xs]);
    sidRef.current = s.id;
    setSessionId(s.id);
    return s.id;
  };

  /** Send a message and follow the turn; false when it was not accepted (no model, another turn running…). */
  const send = async (content: string, scope: Scope = {}): Promise<boolean> => {
    const text = content.trim();
    if (!text) return false;
    if (streamingId !== null) {
      toast("L’assistente sta ancora lavorando: attendi o premi Stop.", "error");
      return false;
    }
    try {
      const sid = await ensureSession();
      const r = await post<{ message: Message; reply: Message }>(`/api/chat/sessions/${sid}/messages`, { content: text, scope });
      const reply = normalize({ ...r.reply, created_at: r.reply.created_at ?? new Date().toISOString() });
      setMessages((ms) => [...ms, normalize(r.message), reply]);
      void attach(reply.id, sid);
      return true;
    } catch (e) {
      toastError(e);
      return false;
    }
  };

  const cancel = async () => {
    if (streamingId === null) return;
    try {
      await post(`/api/chat/replies/${streamingId}/cancel`);
    } catch (e) {
      toastError(e);
    }
  };

  const undo = async (replyId: number): Promise<boolean> => {
    try {
      const r = await post<{ ok: boolean; restored: string[]; skipped: string[] }>(`/api/chat/replies/${replyId}/undo`);
      const files: ChangedFile[] = msgRef.current.find((m) => m.id === replyId)?.change?.files ?? [];
      patch(replyId, (m) => (m.change ? { ...m, change: { ...m.change, status: "undone" } } : m));
      if (r.skipped?.length) toast(`Non ripristinato: ${r.skipped.join(", ")} (modificato dopo)`, "error");
      else toast("Modifiche annullate");
      cb.current(files, true);
      return true;
    } catch (e) {
      toastError(e);
      return false;
    }
  };

  const deleteSession = async () => {
    const sid = sidRef.current;
    if (!sid || streamingId !== null) return;
    try {
      await del(`/api/chat/sessions/${sid}`);
      detach();
      const rest = sessions.filter((s) => s.id !== sid);
      setSessions(rest);
      if (rest[0]) await loadSession(rest[0].id);
      else {
        sidRef.current = null;
        setSessionId(null);
        setMessages([]);
      }
    } catch (e) {
      toastError(e);
    }
  };

  const newSession = async () => {
    if (streamingId !== null) return;
    if (messages.length === 0) return;
    detach();
    try {
      const s = await post<SessionInfo>("/api/chat/sessions", where);
      setSessions((xs) => [s, ...xs]);
      sidRef.current = s.id;
      setSessionId(s.id);
      setMessages([]);
    } catch (e) {
      toastError(e);
    }
  };

  return {
    sessions,
    sessionId,
    messages,
    loading,
    busy: streamingId !== null,
    send,
    cancel,
    undo,
    newSession,
    deleteSession,
    switchSession: (id: number) => (streamingId === null ? loadSession(id) : undefined),
  };
}

export type Assistant = ReturnType<typeof useAssistant>;
