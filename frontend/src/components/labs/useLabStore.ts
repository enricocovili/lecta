// What is written in a lab during the class, the comments and the free notes: edits change the page at once, are saved once a
// minute or when asked (Ctrl+S; at once when the tab is hidden), survive a lost connection (kept in memory and mirrored in
// localStorage, retried with a growing delay). A comment's id is made here, so sending it twice never makes two.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../../lib/api";
import { toastError } from "../ui";

export interface LineAnchor {
  from: number;
  to: number;
  text: string;
}

export interface LabComment {
  id: string;
  file_id: number;
  /** `{}`: the whole file */
  anchor: LineAnchor | Record<string, never>;
  body: string;
  /** 0 until the server has it */
  version: number;
  created_at: string;
}

export type SaveState = "saved" | "pending" | "saving" | "offline";

const AUTOSAVE_MS = 60_000;

interface Backup {
  put: Record<string, { c: LabComment; base: number }>;
  del: string[];
  notes?: { text: string; base: number };
}

const backupKey = (key: string) => `lecta:lab:${key}:unsaved`;

function readBackup(key: string): Backup {
  try {
    const b = JSON.parse(localStorage.getItem(backupKey(key)) || "null") as Backup | null;
    return b && typeof b === "object" ? { put: b.put ?? {}, del: b.del ?? [], notes: b.notes } : { put: {}, del: [] };
  } catch {
    return { put: {}, del: [] };
  }
}

export function isLines(a: LabComment["anchor"]): a is LineAnchor {
  return typeof (a as LineAnchor).from === "number";
}

export function newId(): string {
  // randomUUID exists only on HTTPS pages; getRandomValues everywhere.
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** The id of the lab's free notes in the save queue (comments have UUIDs, so it can't clash). */
const NOTES = "notes";

/** `base`: the lesson's API (`/api/lessons/12`, or a share link's); `key` names the lab in localStorage. */
export function useLabStore(base: string, key: string, initial: LabComment[], initialNotes: { text: string; version: number }) {
  const [comments, setComments] = useState<LabComment[]>(initial);
  const ref = useRef(comments);
  const [notes, setNotesState] = useState(initialNotes.text);
  const notesRef = useRef(notes);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const dirty = useRef(new Set<string>());
  const deleted = useRef(new Set<string>());
  const inflight = useRef(new Set<string>());
  const again = useRef(new Set<string>());
  const versions = useRef(new Map<string, number>([...initial.map((c) => [c.id, c.version] as const), [NOTES, initialNotes.version]]));
  const failures = useRef(0);
  const retry = useRef<number | null>(null);

  const update = useCallback((fn: (cur: LabComment[]) => LabComment[]) => {
    ref.current = fn(ref.current);
    setComments(ref.current);
  }, []);

  const refresh = useCallback(() => {
    setSaveState(failures.current > 0 ? "offline" : inflight.current.size ? "saving" : dirty.current.size || deleted.current.size ? "pending" : "saved");
  }, []);

  const persist = useCallback(() => {
    try {
      const out: Backup = { put: {}, del: [...deleted.current] };
      for (const id of new Set([...dirty.current, ...inflight.current])) {
        if (id === NOTES) {
          out.notes = { text: notesRef.current, base: versions.current.get(NOTES) ?? 0 };
          continue;
        }
        const c = ref.current.find((x) => x.id === id);
        if (c) out.put[id] = { c, base: versions.current.get(id) ?? 0 };
      }
      if (Object.keys(out.put).length || out.del.length || out.notes) localStorage.setItem(backupKey(key), JSON.stringify(out));
      else localStorage.removeItem(backupKey(key));
    } catch {
      /* storage full or unavailable: the in-memory retry still works */
    }
  }, [key]);

  /** A comment is sent once it says something (an empty one being written stays here). */
  const sendable = (c: LabComment) => c.body.trim() !== "" || versions.current.get(c.id);
  const waitingFor = (id: string) => {
    if (id === NOTES) return true;
    const c = ref.current.find((x) => x.id === id);
    return !!c && !!sendable(c);
  };

  const flushOne = useCallback(
    async (id: string): Promise<void> => {
      if (inflight.current.has(id)) {
        again.current.add(id);
        return;
      }
      const isDelete = deleted.current.has(id);
      const isNotes = id === NOTES;
      const c = ref.current.find((x) => x.id === id);
      if (!isDelete && !isNotes && (!c || !sendable(c))) {
        if (!c) dirty.current.delete(id);
        return;
      }
      if (isDelete) deleted.current.delete(id);
      else dirty.current.delete(id);
      inflight.current.add(id);
      refresh();
      try {
        if (isNotes) {
          const r = await api<{ version: number }>(`${base}/lab/notes`, { method: "PUT", json: { notes: notesRef.current } });
          versions.current.set(NOTES, r.version);
        } else if (isDelete) {
          await api(`${base}/lab/comments/${id}`, { method: "DELETE" });
          versions.current.delete(id);
        } else if (c) {
          const r = await api<LabComment>(`${base}/lab/comments/${id}`, { method: "PUT", json: { file_id: c.file_id, anchor: c.anchor, body: c.body } });
          versions.current.set(id, r.version);
          update((cur) => cur.map((x) => (x.id === id ? { ...x, version: r.version } : x)));
        }
        failures.current = 0;
      } catch (e) {
        if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429) {
          // The server refuses it (its file was deleted, say): retrying would not help.
          toastError(e);
          if (e.status === 404 && !isDelete) update((cur) => cur.filter((x) => x.id !== id));
        } else {
          (isDelete ? deleted : dirty).current.add(id);
          failures.current += 1;
          if (retry.current) window.clearTimeout(retry.current);
          retry.current = window.setTimeout(() => void flushAll(), Math.min(30000, 2000 * 2 ** Math.min(failures.current, 4)));
        }
      } finally {
        inflight.current.delete(id);
        if (again.current.delete(id)) void flushOne(id);
        persist();
        refresh();
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [base, persist, refresh, update],
  );

  /** Save everything now (Ctrl+S, the tab is hidden, once a minute). */
  const flushAll = useCallback(async () => {
    for (let round = 0; round < 3; round++) {
      await Promise.all([...dirty.current, ...deleted.current].map((id) => flushOne(id)));
      while (inflight.current.size) await new Promise((r) => setTimeout(r, 60));
      if (![...dirty.current].some(waitingFor) && !deleted.current.size) return true;
    }
    return false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flushOne]);

  const touch = useCallback(
    (id: string) => {
      dirty.current.add(id);
      persist();
      refresh();
    },
    [persist, refresh],
  );

  // Unsaved work of an earlier visit comes back if the server has not moved on meanwhile.
  useEffect(() => {
    const b = readBackup(key);
    let restored = 0;
    if (b.notes && b.notes.base === versions.current.get(NOTES)) {
      notesRef.current = b.notes.text;
      setNotesState(b.notes.text);
      dirty.current.add(NOTES);
      restored += 1;
    }
    update((cur) => {
      let next = cur;
      for (const [id, { c, base: v }] of Object.entries(b.put)) {
        const server = next.find((x) => x.id === id);
        if (server ? server.version !== v : v !== 0) continue;
        next = server ? next.map((x) => (x.id === id ? { ...c, version: x.version } : x)) : [...next, { ...c, version: 0 }];
        dirty.current.add(id);
        restored += 1;
      }
      for (const id of b.del) {
        if (!next.some((x) => x.id === id)) continue;
        next = next.filter((x) => x.id !== id);
        deleted.current.add(id);
        restored += 1;
      }
      return next;
    });
    if (restored) window.setTimeout(() => void flushAll(), 300);
    persist();
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (dirty.current.size || deleted.current.size) void flushAll();
    }, AUTOSAVE_MS);
    const hide = () => document.visibilityState === "hidden" && void flushAll();
    const leave = (e: BeforeUnloadEvent) => {
      if ([...dirty.current].some(waitingFor) || deleted.current.size || inflight.current.size) {
        void flushAll();
        e.preventDefault();
        e.returnValue = "";
      }
    };
    const online = () => {
      failures.current = 0;
      void flushAll();
    };
    document.addEventListener("visibilitychange", hide);
    window.addEventListener("beforeunload", leave);
    window.addEventListener("online", online);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", hide);
      window.removeEventListener("beforeunload", leave);
      window.removeEventListener("online", online);
      if (retry.current) window.clearTimeout(retry.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flushAll]);

  // ------------------------------------------------------------------ edits

  const create = useCallback(
    (fileId: number, anchor: LabComment["anchor"]) => {
      const c: LabComment = { id: newId(), file_id: fileId, anchor, body: "", version: 0, created_at: new Date().toISOString() };
      update((cur) => [...cur, c]);
      touch(c.id);
      return c.id;
    },
    [touch, update],
  );

  const setBody = useCallback(
    (id: string, body: string) => {
      update((cur) => cur.map((c) => (c.id === id ? { ...c, body } : c)));
      touch(id);
      if (failures.current) void flushOne(id);
    },
    [flushOne, touch, update],
  );

  const remove = useCallback(
    (id: string) => {
      update((cur) => cur.filter((c) => c.id !== id));
      dirty.current.delete(id);
      if (versions.current.get(id)) deleted.current.add(id);
      persist();
      refresh();
    },
    [persist, refresh, update],
  );

  const setNotes = useCallback(
    (text: string) => {
      notesRef.current = text;
      setNotesState(text);
      touch(NOTES);
      if (failures.current) void flushOne(NOTES);
    },
    [flushOne, touch],
  );

  /** The comments of a file that is gone leave with it (the server already dropped them). */
  const forgetFile = useCallback(
    (fileId: number) => {
      for (const c of ref.current) if (c.file_id === fileId) dirty.current.delete(c.id);
      update((cur) => cur.filter((c) => c.file_id !== fileId));
      persist();
      refresh();
    },
    [persist, refresh, update],
  );

  return useMemo(
    () => ({ comments, notes, saveState, create, setBody, remove, setNotes, forgetFile, flushAll }),
    [comments, notes, saveState, create, setBody, remove, setNotes, forgetFile, flushAll],
  );
}
