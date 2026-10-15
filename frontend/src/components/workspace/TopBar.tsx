// The top bar of the workspace: where I am, publishing ON/OFF (admins), downloads and the panels. Whoever may only read
// the course sees it marked read-only, with its owner, and no control that changes it.
import { useState } from "react";
import { Icon } from "../icons";
import { NewLessonDialog } from "../lessons/NewLesson";
import Pop from "./Pop";
import PublishControl from "./PublishControl";
import type { Course } from "../types";

export default function TopBar({
  course,
  readOnly,
  owner,
  admin,
  lessons,
  busy,
  compiling,
  railOpen,
  aiOpen,
  onToggleRail,
  onToggleAi,
  onDownloadPdf,
  onSettings,
  onPublishChanged,
}: {
  course: Course;
  readOnly: boolean;
  /** «di <owner>», for a course someone else owns */
  owner: string | null;
  /** publishing is the admins' */
  admin: boolean;
  /** the user may see the course's lessons */
  lessons: boolean;
  busy: boolean;
  compiling: boolean;
  railOpen: boolean;
  aiOpen: boolean;
  onToggleRail: () => void;
  onToggleAi: () => void;
  onDownloadPdf: () => void;
  onSettings: () => void;
  onPublishChanged: () => void;
}) {
  const [newLesson, setNewLesson] = useState(false);
  return (
    <header className="wsb">
      <a className="btn ghost icon wsb-back" href={`/admin/courses/${course.id}`} aria-label="Torna alla materia" title="La materia: lezioni e capitoli">
        <Icon name="arrow-left" />
      </a>
      <button type="button" className="btn ghost icon wsb-rail" onClick={onToggleRail} aria-label="Indice" aria-pressed={railOpen} title="Mostra o nascondi l’indice">
        <Icon name="list" />
      </button>
      <h1 className="wsb-title" title={course.name}>
        {course.name}
      </h1>
      {(readOnly || owner) && (
        <span className="badge wsb-ro" data-testid="read-only" title={readOnly ? "Puoi leggere questa materia, non modificarla" : undefined}>
          <Icon name={readOnly ? "eye" : "user"} />
          <span className="wsb-lbl">{[readOnly && "Sola lettura", owner && `di ${owner}`].filter(Boolean).join(" · ")}</span>
        </span>
      )}

      <div className="wsb-actions">
        {admin && <PublishControl courseId={course.id} courseName={course.name} onChanged={onPublishChanged} />}

        <Pop
          label="Scarica"
          className="wsb-dl"
          summary={
            <>
              <Icon name="download" />
              <span className="wsb-lbl">Scarica</span>
              <Icon name="chevron-down" className="wsb-caret" />
            </>
          }
        >
          <a href={`/api/courses/${course.id}/source.zip`} download data-testid="download-source">
            <Icon name="code" />
            Sorgente LaTeX (.zip)
          </a>
          <button type="button" onClick={onDownloadPdf} disabled={compiling} data-testid="download-pdf">
            <Icon name={compiling ? "loader" : "file-text"} className={compiling ? "spin" : ""} />
            {readOnly ? "PDF (ultima versione)" : "PDF (compila ora)"}
          </button>
        </Pop>

        {!readOnly && (
          <button type="button" className={`btn icon wsb-ai ${aiOpen ? "active" : ""}`} onClick={onToggleAi} aria-pressed={aiOpen} aria-label="Assistente AI" title="Mostra o nascondi l’assistente">
            <Icon name="message" />
            {busy && <span className="wsb-pulse" aria-hidden="true" />}
          </button>
        )}

        <button type="button" className="btn ghost icon" data-theme-toggle aria-label="Cambia tema" title="Tema chiaro / scuro">
          <Icon name="moon" className="i-moon" />
          <Icon name="sun" className="i-sun" />
        </button>

        <Pop label="Altre azioni" className="ghost icon" summary={<Icon name="more" />}>
          {!readOnly && (
            <button type="button" onClick={onSettings}>
              <Icon name="sliders" />
              Impostazioni della materia
            </button>
          )}
          {lessons && (
            <a href={`/admin/courses/${course.id}`}>
              <Icon name="notebook" />
              Lezioni di questa materia
            </a>
          )}
          {!readOnly && (
            <button type="button" onClick={() => setNewLesson(true)}>
              <Icon name="plus" />
              Nuova lezione
            </button>
          )}
          {course.published && (
            <a href={`/courses/${course.slug}`} target="_blank" rel="noreferrer">
              <Icon name="external" />
              Pagina pubblica
            </a>
          )}
          <div className="sep" />
          <a href="/admin">
            <Icon name="home" />
            Home
          </a>
        </Pop>
      </div>
      {newLesson && <NewLessonDialog courseId={course.id} onClose={() => setNewLesson(false)} />}
    </header>
  );
}
