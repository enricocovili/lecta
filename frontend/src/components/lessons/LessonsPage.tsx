// The list of lessons, and the dialog that starts a new one (a subject, a title and, if there are any, the slides).
import { useEffect, useMemo, useRef, useState } from "react";
import { del, fmtDate, fmtSize, get, post, uploadRaw } from "../../lib/api";
import { Icon } from "../icons";
import type { TreeCourse } from "../types";
import { Confirm, Empty, Loading, Modal, Progress, toastError } from "../ui";
import GenerateDialog, { type LessonSummary } from "./GenerateDialog";
import LessonStatusPill, { type LessonStatus } from "./LessonStatus";

interface Row {
  id: number;
  number: number;
  course_id: number;
  course_name: string;
  title: string;
  status: LessonStatus;
  has_pdf: boolean;
  pdf_pages: number;
  page_count: number;
  notes_pages: number;
  ink_pages: number;
  generated_at: string | null;
  chapter_id: number | null;
  updated_at: string;
}

function today(): string {
  return new Date().toLocaleDateString("it-IT", { day: "numeric", month: "long" });
}

export default function LessonsPage() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [courses, setCourses] = useState<TreeCourse[]>([]);
  const [filter, setFilter] = useState("");
  const [creating, setCreating] = useState(false);
  const [toDelete, setToDelete] = useState<Row | null>(null);
  const [toGenerate, setToGenerate] = useState<LessonSummary | null>(null);

  const load = () =>
    get<Row[]>("/api/lessons")
      .then(setRows)
      .catch((e) => {
        toastError(e);
        setRows([]);
      });
  useEffect(() => {
    load();
    get<TreeCourse[]>("/api/tree").then(setCourses).catch(() => undefined);
    const p = new URLSearchParams(location.search);
    if (p.get("course")) setFilter(p.get("course")!);
    if (p.get("new")) setCreating(true);
  }, []);

  const shown = useMemo(() => (rows ?? []).filter((r) => !filter || String(r.course_id) === filter), [rows, filter]);

  return (
    <div className="stack">
      <div className="row between">
        <label className="field les-filter">
          <span className="sr-only">Materia</span>
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filtra per materia">
            <option value="">Tutte le materie</option>
            {courses.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <button className="btn primary" onClick={() => setCreating(true)} data-testid="new-lesson">
          <Icon name="plus" />
          Nuova lezione
        </button>
      </div>

      {rows === null ? (
        <Loading />
      ) : shown.length === 0 ? (
        <Empty icon="notebook">
          Nessuna lezione. Crea una lezione, carica le slide in PDF e prendi appunti durante la lezione: a mano sulle slide e in Markdown accanto.
        </Empty>
      ) : (
        <div className="card flush">
          <div className="rows">
            {shown.map((r) => (
              <div key={r.id} className="pg-row les-list-row" data-testid="lesson-row">
                <span className="status-ico pg-kind-ico">
                  <Icon name="notebook" />
                </span>
                <div className="grow">
                  <a className="pg-row-title" href={`/admin/courses/${r.course_id}/lessons/${r.number}`}>
                    {r.title}
                  </a>
                  <div className="pg-row-sub">
                    {r.course_name} · {r.has_pdf ? `${r.pdf_pages} slide` : `${r.page_count} pagine`} · appunti su {r.notes_pages} · scritte su {r.ink_pages}
                    {r.generated_at ? ` · testo generato il ${fmtDate(r.generated_at)}` : ""}
                  </div>
                </div>
                <LessonStatusPill id={r.id} status={r.status} editable onChange={(status) => setRows((all) => all && all.map((x) => (x.id === r.id ? { ...x, status } : x)))} />
                <span className="pg-time small muted hide-mobile">{fmtDate(r.updated_at)}</span>
                <a className="btn sm" href={`/admin/courses/${r.course_id}/lessons/${r.number}`}>
                  <Icon name="pencil" />
                  Apri
                </a>
                <button
                  className="btn sm"
                  onClick={async () => {
                    const d = await get<{ course_guidelines: string }>(`/api/lessons/${r.id}`);
                    setToGenerate({ ...r, slides: r.has_pdf ? r.pdf_pages : 0, course_guidelines: d.course_guidelines });
                  }}
                  title="Aggiungi appunti e slide al testo da studiare della materia"
                >
                  Aggiungi al testo
                </button>
                <button className="btn ghost icon sm" onClick={() => setToDelete(r)} aria-label={`Elimina ${r.title}`} title="Elimina">
                  <Icon name="trash" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {creating && <NewLesson courses={courses} initialCourse={filter} onClose={() => setCreating(false)} />}
      {toGenerate && <GenerateDialog lesson={toGenerate} onClose={() => setToGenerate(null)} />}
      {toDelete && (
        <Confirm
          title="Elimina la lezione"
          danger
          confirmLabel="Elimina"
          message={<>«{toDelete.title}» e tutto ciò che hai scritto a mano e in Markdown vengono eliminati. Il testo già generato nella materia resta.</>}
          onConfirm={async () => {
            await del(`/api/lessons/${toDelete.id}`);
            await load();
          }}
          onClose={() => setToDelete(null)}
        />
      )}
    </div>
  );
}

function NewLesson({ courses, initialCourse, onClose }: { courses: TreeCourse[]; initialCourse: string; onClose: () => void }) {
  const [courseId, setCourseId] = useState(initialCourse || (courses.length === 1 ? String(courses[0].id) : ""));
  const [title, setTitle] = useState(`Lezione del ${today()}`);
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!courseId && courses.length === 1) setCourseId(String(courses[0].id));
  }, [courses, courseId]);

  const create = async () => {
    setBusy(true);
    let id: number | null = null;
    try {
      const l = await post<{ id: number; number: number }>("/api/lessons", { course_id: Number(courseId), title: title.trim() });
      id = l.id;
      if (file) {
        setProgress(0);
        await uploadRaw(`/api/lessons/${id}/slides?name=${encodeURIComponent(file.name)}`, file, setProgress);
      }
      location.href = `/admin/courses/${courseId}/lessons/${l.number}`;
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
          <button className="btn primary" onClick={create} disabled={busy || !courseId || !title.trim()} data-testid="create-lesson">
            {busy ? <Icon name="loader" className="spin" /> : <Icon name="check" />}
            Crea e apri
          </button>
        </>
      }
    >
      <div className="stack">
        <label className="field">
          Materia
          <select value={courseId} onChange={(e) => setCourseId(e.target.value)} disabled={busy} data-testid="lesson-course">
            <option value="">Scegli la materia…</option>
            {courses.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {courses.length === 0 && (
          <div className="alert warn small">
            <Icon name="alert-triangle" />
            <span className="grow">
              Non c’è ancora nessuna materia. <a href="/admin/courses?new=1">Creane una</a>.
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
