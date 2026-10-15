// The dialog that starts a new lesson (a subject, a title and, if there are any, the slides), opened from the home page,
// the course page and the course's text (decision 135).
import { useEffect, useRef, useState, type ReactNode } from "react";
import { del, fmtDay, fmtSize, get, post, uploadRaw } from "../../lib/api";
import { Icon } from "../icons";
import type { TreeCourse } from "../types";
import { Modal, Progress, toastError } from "../ui";

/** The dialog; `courseId` preselects the subject. It lists the subjects the user may write in. */
export function NewLessonDialog({ courseId, onClose }: { courseId?: number; onClose: () => void }) {
  const [courses, setCourses] = useState<TreeCourse[] | null>(null);
  const [course, setCourse] = useState(courseId ? String(courseId) : "");
  const [title, setTitle] = useState(`Lezione del ${fmtDay(new Date())}`);
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    get<TreeCourse[]>("/api/tree")
      .then((t) => {
        const mine = t.filter((c) => c.can_write);
        setCourses(mine);
        if (!courseId && mine.length === 1) setCourse(String(mine[0].id));
      })
      .catch((e) => {
        toastError(e);
        setCourses([]);
      });
  }, [courseId]);

  const create = async () => {
    setBusy(true);
    let id: number | null = null;
    try {
      const l = await post<{ id: number; number: number }>("/api/lessons", { course_id: Number(course), title: title.trim() });
      id = l.id;
      if (file) {
        setProgress(0);
        await uploadRaw(`/api/lessons/${id}/slides?name=${encodeURIComponent(file.name)}`, file, setProgress);
      }
      location.href = `/admin/courses/${course}/lessons/${l.number}`;
    } catch (e) {
      // A lesson whose slides could not be attached is removed again: nothing was written in it yet.
      if (id !== null) await del(`/api/lessons/${id}`).catch(() => undefined);
      toastError(e);
      setBusy(false);
      setProgress(null);
    }
  };

  return (
    <Modal
      title="Nuova lezione"
      onClose={busy ? () => undefined : onClose}
      actions={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Annulla
          </button>
          <button className="btn primary" onClick={create} disabled={busy || !course || !title.trim()} data-testid="create-lesson">
            {busy ? <Icon name="loader" className="spin" /> : <Icon name="check" />}
            Crea e apri
          </button>
        </>
      }
    >
      <div className="stack">
        <label className="field">
          Materia
          <select value={course} onChange={(e) => setCourse(e.target.value)} disabled={busy || courses === null} data-testid="lesson-course">
            <option value="">{courses === null ? "Caricamento…" : "Scegli la materia…"}</option>
            {(courses ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {courses?.length === 0 && (
          <div className="alert warn small">
            <Icon name="alert-triangle" />
            <span className="grow">
              Non c’è ancora nessuna materia. <a href="/admin?new=1">Creane una</a>.
            </span>
          </div>
        )}
        <label className="field">
          Titolo
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} disabled={busy} data-testid="lesson-title" />
        </label>
        <div className="field">
          Slide <span className="hint">PDF, facoltativo: senza, hai pagine bianche su cui scrivere</span>
          <div className="row">
            <button type="button" className="btn" onClick={() => input.current?.click()} disabled={busy}>
              <Icon name="upload" />
              {file ? "Cambia PDF" : "Scegli il PDF delle slide"}
            </button>
            {file && (
              <span className="small muted">
                {file.name} · {fmtSize(file.size)}
              </span>
            )}
          </div>
          <input
            ref={input}
            type="file"
            accept="application/pdf,.pdf"
            hidden
            data-testid="lesson-pdf"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              e.target.value = "";
            }}
          />
          {progress !== null && <Progress value={progress} tone="warn" />}
        </div>
      </div>
    </Modal>
  );
}

/** A button that opens the dialog. */
export function NewLessonButton({ courseId, className = "btn", testId, children }: { courseId?: number; className?: string; testId?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)} data-testid={testId}>
        {children}
      </button>
      {open && <NewLessonDialog courseId={courseId} onClose={() => setOpen(false)} />}
    </>
  );
}
