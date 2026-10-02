// Small helpers shared by the workspace pages (courses, jobs, upload, inbox).
import { useEffect, useRef, type ReactNode } from "react";
import { get } from "../lib/api";
import { Icon } from "./icons";
import { jobStage, translateProgress } from "./jobtext";
import { StatusPill, useApi, type Tone } from "./ui";

/** Per-course state from /api/dashboard (`courses[]`). */
export interface CourseState {
  id: number;
  name: string;
  slug: string;
  published: boolean;
  updated_at: string;
  chapter_count: number;
  status: "ok" | "working" | "error" | string;
  reason: string | null;
  active_job: { id: number; kind: string; title: string; status: string; progress: number; progress_text: string } | null;
  last_build: { id: number; status: string; at: string; errors: number; first_error: { file: string | null; line: number | null; message: string; chapter_id?: number; chapter_title?: string } | null } | null;
  failed_job?: { id: number; title: string; error: string | null };
  sources: number;
}

export function useCourseStates() {
  return useApi(() => get<{ courses: CourseState[] }>("/api/dashboard").then((d) => d.courses ?? []), []);
}

export const COURSE_TONE: Record<string, Tone> = { ok: "ok", working: "warn", error: "danger" };
export const COURSE_LABEL: Record<string, string> = { ok: "Completata", working: "In lavorazione", error: "Errore" };

export function CoursePill({ status, size = "" }: { status: string | undefined; size?: "" | "sm" }) {
  const s = status ?? "ok";
  return <StatusPill tone={COURSE_TONE[s] ?? ""} label={COURSE_LABEL[s] ?? s} size={size} />;
}

/** "Pubblicata" (globe) / "Privata" (lock). */
export function PublishedFlag({ published }: { published: boolean }) {
  return (
    <span className={`pg-pubflag ${published ? "on" : ""}`}>
      <Icon name={published ? "globe" : "lock"} />
      {published ? "Pubblicata" : "Privata"}
    </span>
  );
}

/** "oggi alle 07:24", "ieri alle 18:02", "il 3 set 2025". */
export function fmtUpdated(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  const now = new Date();
  const time = d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `oggi alle ${time}`;
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return `ieri alle ${time}`;
  return "il " + d.toLocaleDateString("it-IT", { day: "numeric", month: "short", ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
}

/** One-line state of a course for cards and headers ("Errore di compilazione · Memoria virtuale, riga 11"). */
export function courseStateText(st: CourseState | undefined): { text: string; tone: Tone } | null {
  if (!st) return null;
  if (st.status === "error" && st.reason === "compile") {
    const fe = st.last_build?.first_error;
    const where = fe ? [fe.chapter_title ?? fe.file, fe.line ? `riga ${fe.line}` : ""].filter(Boolean).join(", ") : "";
    return { text: `Errore di compilazione${where ? ` · ${where}` : ""}`, tone: "danger" };
  }
  if (st.status === "error" && st.failed_job) return { text: `Attività non riuscita: ${st.failed_job.error ?? st.failed_job.title}`, tone: "danger" };
  if (st.status === "working" && st.active_job) {
    const s = jobStage(st.active_job);
    return { text: `${s.label}${s.steps > 1 ? ` · ${s.step} di ${s.steps}` : ""}`, tone: "warn" };
  }
  return null;
}

// ------------------------------------------------------------------ jobs

export const JOB_TONE: Record<string, Tone> = { succeeded: "ok", failed: "danger", running: "warn", queued: "warn", cancelled: "" };
export const JOB_LABEL: Record<string, string> = {
  succeeded: "Completato",
  failed: "Errore",
  running: "In lavorazione",
  queued: "In lavorazione",
  cancelled: "Annullato",
};

export function JobPill({ status, size = "" }: { status: string; size?: "" | "sm" }) {
  return (
    <span className="pg-jobpill" data-status={status}>
      <StatusPill tone={JOB_TONE[status] ?? ""} label={JOB_LABEL[status] ?? status} size={size} />
    </span>
  );
}

/** "Generazione LaTeX · 2 di 3" */
export function stageText(job: Parameters<typeof jobStage>[0]): string {
  const s = jobStage(job);
  return s.steps > 1 && job.status !== "succeeded" ? `${s.label} · ${s.step} di ${s.steps}` : s.label;
}

export { translateProgress };

/** Icon for a source file kind (pdf, image, markdown, text, zip …). */
export function kindIcon(kind: string): string {
  return kind === "image" ? "image" : kind === "zip" || kind === "archive" ? "archive" : kind === "pdf" ? "file-text" : "file";
}

// ------------------------------------------------------------------ ⋯ menu

export interface MenuItem {
  label: ReactNode;
  icon?: string;
  onClick: () => void;
  danger?: boolean;
  hidden?: boolean;
}

/** A "⋯" button with a small popover menu (closes on outside click and on pick). */
export function MoreMenu({ items, label = "Altre azioni", size = "sm" }: { items: MenuItem[]; label?: string; size?: "sm" | "xs" | "" }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const on = (e: MouseEvent) => {
      if (ref.current?.open && !ref.current.contains(e.target as Node)) ref.current.open = false;
    };
    document.addEventListener("click", on);
    return () => document.removeEventListener("click", on);
  }, []);
  return (
    <details className="dropdown pg-more" ref={ref}>
      <summary className={`btn ghost icon ${size}`} aria-label={label} title={label}>
        <Icon name="more" />
      </summary>
      <div className="menu-pop">
        {items
          .filter((i) => !i.hidden)
          .map((i, n) => (
            <button
              key={n}
              type="button"
              className={i.danger ? "pg-danger" : ""}
              onClick={() => {
                if (ref.current) ref.current.open = false;
                i.onClick();
              }}
            >
              {i.icon && <Icon name={i.icon} />}
              {i.label}
            </button>
          ))}
      </div>
    </details>
  );
}
