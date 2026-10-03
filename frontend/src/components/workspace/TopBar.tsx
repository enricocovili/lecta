// The top bar of the workspace: where I am, publishing ON/OFF, downloads and the panels.
import { Icon } from "../icons";
import Pop from "./Pop";
import PublishControl from "./PublishControl";
import type { Course } from "../types";

export default function TopBar({
  course,
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

      <div className="wsb-actions">
        <PublishControl courseId={course.id} courseName={course.name} onChanged={onPublishChanged} />

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
            PDF (compila ora)
          </button>
        </Pop>

        <button type="button" className={`btn icon wsb-ai ${aiOpen ? "active" : ""}`} onClick={onToggleAi} aria-pressed={aiOpen} aria-label="Assistente AI" title="Mostra o nascondi l’assistente">
          <Icon name="message" />
          {busy && <span className="wsb-pulse" aria-hidden="true" />}
        </button>

        <button type="button" className="btn ghost icon" data-theme-toggle aria-label="Cambia tema" title="Tema chiaro / scuro">
          <Icon name="moon" className="i-moon" />
          <Icon name="sun" className="i-sun" />
        </button>

        <Pop label="Altre azioni" className="ghost icon" summary={<Icon name="more" />}>
          <button type="button" onClick={onSettings}>
            <Icon name="sliders" />
            Impostazioni della materia
          </button>
          <a href={`/admin/lessons?course=${course.id}`}>
            <Icon name="notebook" />
            Lezioni di questa materia
          </a>
          <a href={`/admin/lessons?new=1&course=${course.id}`}>
            <Icon name="plus" />
            Nuova lezione
          </a>
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
    </header>
  );
}
