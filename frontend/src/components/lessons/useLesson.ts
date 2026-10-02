// The pages of a lesson while they are being written: edits apply at once, are saved once a minute or when asked (Ctrl+S; and
// at once when the tab is hidden or the structure changes), survive a lost connection (kept in memory and mirrored in localStorage, retried with a growing delay) and
// can be undone: one history for strokes, erasing, typed notes and removed pages, in the order things were done.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, post, del } from "../../lib/api";
import { toastError } from "../ui";
import { applyPatch, caretAfter, diffPatch, mergeable, type NotesPatch } from "./history";
import type { Stroke } from "./ink";

export interface PageState {
  id: number;
  position: number;
  kind: "slide" | "blank";
  slide_page: number | null;
  ratio: number;
  notes: string;
  ink: Stroke[];
  version: number;
}

export type SaveState = "saved" | "pending" | "saving" | "offline";

type Action =
  | { type: "add"; pid: number; stroke: Stroke }
  | { type: "erase"; pid: number; items: { index: number; stroke: Stroke }[] }
  | { type: "notes"; pid: number; patch: NotesPatch; at: number }
  | { type: "remove"; pid: number; page: PageState; index: number };

/** Unsaved edits are sent at this rhythm (they are mirrored in localStorage at once, so nothing is lost meanwhile). */
const AUTOSAVE_MS = 60_000;
const HISTORY = 300;

const renumber = (list: PageState[]) => list.map((p, i) => ({ ...p, position: i + 1 }));

interface Backup {
  [pid: string]: { notes?: string; ink?: Stroke[]; base: number };
}

/** Where a lesson's pages live: the owner's API (`/api/lessons/12`) or a share link's (`/api/public/lesson/<token>`). */
export interface LessonSource {
  base: string;
  /** names this lesson in localStorage */
  key: string;
}

function backupKey(key: string): string {
  return `lecta:lesson:${key}:unsaved`;
}

function readBackup(key: string): Backup {
  try {
    return JSON.parse(localStorage.getItem(backupKey(key)) || "{}") as Backup;
  } catch {
    return {};
  }
}

/** `poll`: every so many ms the pages other people saved (an owner with share links, a visitor with a write link, a reader) are brought in. */
export function useLessonPages(src: LessonSource, initial: PageState[], poll: number | null = null, onTitle?: (title: string) => void) {
  const { base } = src;
  const [pages, setPages] = useState<PageState[]>(() => initial);
  const pagesRef = useRef<PageState[]>(pages);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [hist, setHist] = useState({ undo: 0, redo: 0 });

  const dirty = useRef(new Map<number, { notes: boolean; ink: boolean }>());
  const timers = useRef(new Map<number, number>());
  const inflight = useRef(new Set<number>());
  const again = useRef(new Set<number>());
  const versions = useRef(new Map<number, number>(initial.map((p) => [p.id, p.version])));
  const failures = useRef(0);
  const undoStack = useRef<Action[]>([]);
  const redoStack = useRef<Action[]>([]);
  // Undo and redo run one after the other (restoring a page asks the server), in the order they were pressed.
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const removed = useRef(new Set<number>());
  const showHist = useCallback(() => {
    const next = { undo: undoStack.current.length, redo: redoStack.current.length };
    setHist((h) => (h.undo === next.undo && h.redo === next.redo ? h : next));
  }, []);

  const update = useCallback((fn: (cur: PageState[]) => PageState[]) => {
    const next = fn(pagesRef.current);
    pagesRef.current = next;
    setPages(next);
  }, []);
  const patchPage = useCallback(
    (pid: number, fn: (p: PageState) => PageState) => update((cur) => cur.map((p) => (p.id === pid ? fn(p) : p))),
    [update],
  );

  const refreshState = useCallback(() => {
    setSaveState(failures.current > 0 ? "offline" : inflight.current.size ? "saving" : dirty.current.size ? "pending" : "saved");
  }, []);

  const persistBackup = useCallback(() => {
    try {
      const out: Backup = {};
      const ids = new Set([...dirty.current.keys(), ...inflight.current]);
      for (const pid of ids) {
        const p = pagesRef.current.find((x) => x.id === pid);
        if (!p) continue;
        const d = dirty.current.get(pid) ?? { notes: true, ink: true };
        out[pid] = { base: versions.current.get(pid) ?? p.version, ...(d.notes ? { notes: p.notes } : {}), ...(d.ink ? { ink: p.ink } : {}) };
      }
      if (Object.keys(out).length) localStorage.setItem(backupKey(src.key), JSON.stringify(out));
      else localStorage.removeItem(backupKey(src.key));
    } catch {
      /* storage full or unavailable: the in-memory retry still works */
    }
  }, [src.key]);

  const schedule = useCallback(
    (pid: number, delay: number) => {
      const old = timers.current.get(pid);
      if (old) window.clearTimeout(old);
      timers.current.set(
        pid,
        window.setTimeout(() => {
          timers.current.delete(pid);
          void flushPage(pid);
        }, delay),
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const flushPage = useCallback(
    async (pid: number): Promise<void> => {
      if (inflight.current.has(pid)) {
        again.current.add(pid);
        return;
      }
      const d = dirty.current.get(pid);
      const page = pagesRef.current.find((p) => p.id === pid);
      if (!d) return;
      if (!page) {
        dirty.current.delete(pid);
        refreshState();
        return;
      }
      dirty.current.delete(pid);
      inflight.current.add(pid);
      refreshState();
      try {
        const r = await api<{ version: number }>(`${base}/pages/${pid}`, {
          method: "PUT",
          json: { ...(d.notes ? { notes: page.notes } : {}), ...(d.ink ? { ink: page.ink } : {}) },
        });
        versions.current.set(pid, r.version);
        failures.current = 0;
      } catch (e) {
        if (e instanceof ApiError && e.status === 404 && removed.current.has(pid)) {
          /* the page was removed while its last edit was on the way */
        } else if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429) {
          toastError(e); // the server refuses this page: retrying would not help
        } else {
          const cur = dirty.current.get(pid) ?? { notes: false, ink: false };
          dirty.current.set(pid, { notes: cur.notes || d.notes, ink: cur.ink || d.ink });
          failures.current += 1;
          schedule(pid, Math.min(30000, 2000 * 2 ** Math.min(failures.current, 4)));
        }
      } finally {
        inflight.current.delete(pid);
        if (again.current.delete(pid)) schedule(pid, 0);
        persistBackup();
        refreshState();
      }
    },
    [base, persistBackup, refreshState, schedule],
  );

  const markDirty = useCallback(
    (pid: number, what: "notes" | "ink") => {
      const d = dirty.current.get(pid) ?? { notes: false, ink: false };
      d[what] = true;
      dirty.current.set(pid, d);
      persistBackup();
      refreshState();
      if (failures.current) schedule(pid, 2000); // otherwise the once-a-minute autosave (or Ctrl+S) sends it
    },
    [persistBackup, refreshState, schedule],
  );

  /** Save everything now (Ctrl+S, before leaving the page or generating the text). */
  const flushAll = useCallback(async () => {
    for (const [pid, t] of timers.current) {
      window.clearTimeout(t);
      timers.current.delete(pid);
    }
    for (let round = 0; round < 3; round++) {
      await Promise.all([...dirty.current.keys()].map((pid) => flushPage(pid)));
      while (inflight.current.size) await new Promise((r) => setTimeout(r, 60));
      if (!dirty.current.size) return true;
    }
    return dirty.current.size === 0;
  }, [flushPage]);

  // Unsaved work of an earlier visit (the connection dropped, the tab was closed) comes back if the server has not moved on.
  useEffect(() => {
    const backup = readBackup(src.key);
    let restored = 0;
    update((cur) =>
      cur.map((p) => {
        const b = backup[p.id];
        if (!b || b.base !== p.version) return p;
        restored += 1;
        const d = dirty.current.get(p.id) ?? { notes: false, ink: false };
        if (b.notes !== undefined) d.notes = true;
        if (b.ink !== undefined) d.ink = true;
        dirty.current.set(p.id, d);
        return { ...p, notes: b.notes ?? p.notes, ink: b.ink ?? p.ink };
      }),
    );
    if (restored) {
      for (const pid of dirty.current.keys()) schedule(pid, 300);
      refreshState();
    } else {
      try {
        localStorage.removeItem(backupKey(src.key));
      } catch {
        /* ignore */
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src.key]);

  // Once a minute whatever is unsaved is sent.
  useEffect(() => {
    const id = window.setInterval(() => {
      if (dirty.current.size) void flushAll();
    }, AUTOSAVE_MS);
    return () => window.clearInterval(id);
  }, [flushAll]);

  useEffect(() => {
    const hide = () => {
      if (document.visibilityState === "hidden") void flushAll();
    };
    const leave = (e: BeforeUnloadEvent) => {
      if (dirty.current.size || inflight.current.size) {
        void flushAll();
        e.preventDefault();
        e.returnValue = "";
      }
    };
    const online = () => {
      if (dirty.current.size) {
        failures.current = 0;
        for (const pid of dirty.current.keys()) schedule(pid, 0);
      }
    };
    document.addEventListener("visibilitychange", hide);
    window.addEventListener("beforeunload", leave);
    window.addEventListener("online", online);
    return () => {
      document.removeEventListener("visibilitychange", hide);
      window.removeEventListener("beforeunload", leave);
      window.removeEventListener("online", online);
    };
  }, [flushAll, schedule]);

  // ------------------------------------------------------------------ following what others save

  const [gone, setGone] = useState(false);
  const syncing = useRef(false);
  const churn = useRef(false); // pages were added or removed here while a sync was on the way: its answer is out of date
  const syncNow = useCallback(async () => {
    if (syncing.current) return;
    syncing.current = true;
    churn.current = false;
    try {
      const known: Record<number, number> = {};
      for (const p of pagesRef.current) known[p.id] = versions.current.get(p.id) ?? p.version;
      const r = await api<{ title: string; order: number[]; pages: PageState[] }>(`${base}/sync`, { method: "POST", json: { known } });
      if (churn.current) return;
      const incoming = new Map(r.pages.map((p) => [p.id, p]));
      // The page one is working on (unsaved or being saved) is kept as it is: saving it sends it on.
      const mine = (id: number) => dirty.current.has(id) || inflight.current.has(id);
      const changed: number[] = [];
      update((cur) => {
        const local = new Map(cur.map((p) => [p.id, p]));
        const next: PageState[] = [];
        let same = r.order.length === cur.length;
        r.order.forEach((id, i) => {
          const l = local.get(id);
          const remote = incoming.get(id);
          if (l && (mine(id) || !remote || remote.version === versions.current.get(id))) {
            next.push(l);
          } else if (remote) {
            versions.current.set(id, remote.version);
            changed.push(id);
            next.push({ ...remote, notes: remote.notes ?? "", ink: remote.ink ?? [] });
          } else if (l) next.push(l);
          if (cur[i]?.id !== id) same = false;
        });
        return same && !changed.length ? cur : renumber(next);
      });
      // What the history knows of a page that others changed would no longer fit it.
      if (changed.length) {
        for (const stack of [undoStack.current, redoStack.current]) {
          for (let i = stack.length - 1; i >= 0; i--) if (stack[i].type !== "remove" && changed.includes(stack[i].pid)) stack.splice(i, 1);
        }
        showHist();
      }
      onTitle?.(r.title);
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 403)) setGone(true);
    } finally {
      syncing.current = false;
    }
  }, [base, onTitle, showHist, update]);

  useEffect(() => {
    if (!poll || gone) return;
    const id = window.setInterval(() => document.visibilityState === "visible" && void syncNow(), poll);
    const back = () => document.visibilityState === "visible" && void syncNow();
    document.addEventListener("visibilitychange", back);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", back);
    };
  }, [poll, gone, syncNow]);

  // ------------------------------------------------------------------ edits

  const pushHistory = useCallback(
    (a: Action) => {
      undoStack.current.push(a);
      if (undoStack.current.length > HISTORY) undoStack.current.shift();
      redoStack.current = [];
      showHist();
    },
    [showHist],
  );

  const setNotes = useCallback(
    (pid: number, notes: string) => {
      const prev = pagesRef.current.find((p) => p.id === pid)?.notes;
      if (prev === undefined || prev === notes) return;
      patchPage(pid, (p) => ({ ...p, notes }));
      markDirty(pid, "notes");
      const edit = diffPatch(prev, notes);
      const now = Date.now();
      const last = undoStack.current[undoStack.current.length - 1];
      if (last?.type === "notes" && last.pid === pid && mergeable(edit, last.at, now)) {
        // Typing goes on: the same step grows (its patch is made again from the text as it was before the step).
        const patch = diffPatch(applyPatch(prev, last.patch, true), notes);
        redoStack.current = [];
        if (patch.del === "" && patch.ins === "") undoStack.current.pop();
        else {
          last.patch = patch;
          last.at = now;
        }
        showHist();
      } else {
        pushHistory({ type: "notes", pid, patch: edit, at: mergeable(edit, now, now) ? now : -Infinity });
      }
    },
    [markDirty, patchPage, pushHistory, showHist],
  );

  const addStroke = useCallback(
    (pid: number, stroke: Stroke) => {
      patchPage(pid, (p) => ({ ...p, ink: [...p.ink, stroke] }));
      pushHistory({ type: "add", pid, stroke });
      markDirty(pid, "ink");
    },
    [markDirty, patchPage, pushHistory],
  );

  const eraseStrokes = useCallback(
    (pid: number, indices: number[]) => {
      const page = pagesRef.current.find((p) => p.id === pid);
      if (!page) return;
      const items = [...indices].sort((a, b) => a - b).filter((i) => page.ink[i]).map((index) => ({ index, stroke: page.ink[index] }));
      if (!items.length) return;
      const gone = new Set(items.map((i) => i.stroke));
      patchPage(pid, (p) => ({ ...p, ink: p.ink.filter((s) => !gone.has(s)) }));
      pushHistory({ type: "erase", pid, items });
      markDirty(pid, "ink");
    },
    [markDirty, patchPage, pushHistory],
  );

  // ------------------------------------------------------------------ structure

  const addBlankAfter = useCallback(
    async (afterId: number | null) => {
      await flushAll();
      const created = await post<Omit<PageState, "ink" | "notes"> & { ink: Stroke[]; notes: string }>(`${base}/pages`, { after_page_id: afterId });
      versions.current.set(created.id, created.version);
      churn.current = true;
      update((cur) => {
        const at = afterId === null ? cur.length : cur.findIndex((p) => p.id === afterId) + 1;
        const next = [...cur];
        next.splice(at, 0, { ...created, notes: created.notes ?? "", ink: created.ink ?? [] });
        return renumber(next);
      });
      return created.id;
    },
    [flushAll, base, update],
  );

  /** Take a page out of the lesson (on the server and here) without touching the history. */
  const dropPage = useCallback(
    async (pid: number) => {
      await del(`${base}/pages/${pid}`);
      churn.current = true;
      removed.current.add(pid);
      const t = timers.current.get(pid);
      if (t) window.clearTimeout(t);
      timers.current.delete(pid);
      dirty.current.delete(pid);
      again.current.delete(pid);
      update((cur) => renumber(cur.filter((p) => p.id !== pid)));
      persistBackup();
      refreshState();
    },
    [base, persistBackup, refreshState, update],
  );

  /** Put a removed page back, with its own id, at its place. */
  const bringBack = useCallback(
    async (page: PageState, index: number) => {
      const r = await post<{ version: number }>(`${base}/pages/restore`, {
        id: page.id, kind: page.kind, slide_page: page.slide_page, ratio: page.ratio, position: index + 1, notes: page.notes, ink: page.ink,
      });
      removed.current.delete(page.id);
      churn.current = true;
      versions.current.set(page.id, r.version);
      update((cur) => {
        const next = [...cur];
        next.splice(Math.min(index, next.length), 0, { ...page, version: r.version });
        return renumber(next);
      });
    },
    [base, update],
  );

  const removePage = useCallback(
    async (pid: number) => {
      const index = pagesRef.current.findIndex((p) => p.id === pid);
      if (index < 0) return;
      const page = pagesRef.current[index];
      await dropPage(pid);
      pushHistory({ type: "remove", pid, page, index });
    },
    [dropPage, pushHistory],
  );

  // ------------------------------------------------------------------ undo / redo

  const step = useCallback(
    async (a: Action, inverse: boolean) => {
      const reveal = (pid: number) => document.getElementById(`lesson-page-${pid}`)?.scrollIntoView({ block: "nearest" });
      if (a.type === "remove") {
        if (inverse) await bringBack(a.page, a.index);
        else await dropPage(a.pid);
        if (inverse) reveal(a.pid);
        return;
      }
      if (a.type === "notes") {
        patchPage(a.pid, (p) => ({ ...p, notes: applyPatch(p.notes, a.patch, inverse) }));
        markDirty(a.pid, "notes");
        reveal(a.pid);
        // The field was rewritten: the caret goes where the change was (only if the user is typing in that field).
        requestAnimationFrame(() => {
          const field = document.querySelector<HTMLTextAreaElement>(`#lesson-page-${a.pid} textarea`);
          if (field && document.activeElement === field) field.setSelectionRange(caretAfter(a.patch, inverse), caretAfter(a.patch, inverse));
        });
        return;
      }
      const drop = (strokes: Stroke[]) => patchPage(a.pid, (p) => ({ ...p, ink: p.ink.filter((s) => !strokes.includes(s)) }));
      if (a.type === "add") {
        if (inverse) drop([a.stroke]);
        else patchPage(a.pid, (p) => ({ ...p, ink: [...p.ink, a.stroke] }));
      } else if (inverse) {
        patchPage(a.pid, (p) => {
          const ink = [...p.ink];
          for (const it of a.items) ink.splice(Math.min(it.index, ink.length), 0, it.stroke);
          return { ...p, ink };
        });
      } else {
        drop(a.items.map((i) => i.stroke));
      }
      markDirty(a.pid, "ink");
      reveal(a.pid);
    },
    [bringBack, dropPage, markDirty, patchPage],
  );

  const travel = useCallback(
    (from: Action[], to: Action[], inverse: boolean) => {
      const a = from.pop();
      if (!a) return;
      to.push(a);
      showHist();
      queue.current = queue.current.then(async () => {
        try {
          await step(a, inverse);
        } catch (e) {
          toastError(e);
          const at = to.lastIndexOf(a);
          if (at >= 0) to.splice(at, 1);
          from.push(a);
          showHist();
        }
      });
    },
    [showHist, step],
  );
  const undo = useCallback(() => travel(undoStack.current, redoStack.current, true), [travel]);
  const redo = useCallback(() => travel(redoStack.current, undoStack.current, false), [travel]);

  useEffect(
    () => () => {
      for (const t of timers.current.values()) window.clearTimeout(t);
    },
    [],
  );

  return useMemo(
    () => ({ pages, saveState, gone, syncNow, setNotes, addStroke, eraseStrokes, undo, redo, canUndo: hist.undo > 0, canRedo: hist.redo > 0, addBlankAfter, removePage, flushAll }),
    [pages, saveState, gone, syncNow, setNotes, addStroke, eraseStrokes, undo, redo, hist, addBlankAfter, removePage, flushAll],
  );
}
