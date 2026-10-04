import { useEffect, useState } from "react";
import { get, post } from "../lib/api";
import { Icon } from "./icons";
import { COURSE_TONE, CoursePill, courseStateText, fmtUpdated, PublishedFlag, useCourseStates, type CourseState } from "./pagekit";
import type { Course } from "./types";
import { Dot, Empty, ErrorBox, Loading, Modal, Seg, toastError, useApi, useLocalStorage } from "./ui";

export const LANGUAGES = [
  { code: "it", label: "Italiano" },
  { code: "en", label: "Inglese" },
  { code: "fr", label: "Francese" },
  { code: "de", label: "Tedesco" },
  { code: "es", label: "Spagnolo" },
  { code: "pt", label: "Portoghese" },
];

export function NewCourseModal({ onClose, onCreated }: { onClose: () => void; onCreated: (c: Course) => void }) {
  const [name, setName] = useState("");
  const [year, setYear] = useState(() => {
    const d = new Date();
    const y = d.getMonth() >= 8 ? d.getFullYear() : d.getFullYear() - 1;
    return `${y}/${String(y + 1).slice(2)}`;
  });
  const [language, setLanguage] = useState("it");
  const [tags, setTags] = useState("");
  const [chapters, setChapters] = useState("");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    try {
      const c = await post<Course>("/api/courses", {
        name,
        academic_year: year || null,
        language,
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        chapters: chapters
          .split("\n")
          .map((t) => t.trim())
          .filter(Boolean),
      });
      window.dispatchEvent(new Event("lecta:tree-changed"));
      onCreated(c);
    } catch (e) {
      toastError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Nuova materia"
      onClose={onClose}
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Annulla
          </button>
          <button className="btn primary" disabled={!name.trim() || busy} onClick={create}>
            <Icon name="plus" />
            Crea materia
          </button>
        </>
      }
    >
      <div className="stack">
        <label className="field">
          Nome
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} autoFocus placeholder="Analisi Matematica I" />
        </label>
        <div className="form-grid">
          <label className="field">
            Anno accademico
            <input type="text" value={year} onChange={(e) => setYear(e.target.value)} />
          </label>
          <label className="field">
            Lingua
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="field">
          Tag <span className="hint">separati da virgola</span>
          <input type="text" value={tags} onChange={(e) => setTags(e.target.value)} />
        </label>
        <label className="field">
          Capitoli <span className="hint">facoltativo, un titolo per riga</span>
          <textarea className="prose" rows={4} value={chapters} onChange={(e) => setChapters(e.target.value)} placeholder={"Limiti\nDerivate\nIntegrali"} />
        </label>
      </div>
    </Modal>
  );
}

function pdfHref(c: { id: number; slug: string; published: boolean }) {
  return c.published ? `/api/public/courses/${c.slug}.pdf` : `/api/courses/${c.id}/pdf`;
}

function chapterCount(c: Course, st: CourseState | undefined) {
  const n = c.chapter_count ?? st?.chapter_count ?? 0;
  return `${n} ${n === 1 ? "capitolo" : "capitoli"}`;
}

function metaLine(c: Course, st: CourseState | undefined) {
  return `${c.academic_year ? `${c.academic_year} · ` : ""}${chapterCount(c, st)} · aggiornata ${fmtUpdated(c.updated_at)}`;
}

function CourseCard({ c, st }: { c: Course; st: CourseState | undefined }) {
  const state = courseStateText(st);
  return (
    <div className="card pg-course-card">
      <div className="row between pg-nw">
        <CoursePill status={st?.status} />
        <PublishedFlag published={c.published} />
      </div>
      <a className="card-link pg-course-name" href={`/admin/courses/${c.id}`}>
        {c.name}
      </a>
      <div className="pg-course-meta">
        {state ? (
          <>
            <span className={state.tone === "danger" ? "text-danger" : ""}>{state.text}</span>
            <span> · {chapterCount(c, st)}</span>
          </>
        ) : (
          metaLine(c, st)
        )}
      </div>
      {c.tags.length > 0 && (
        <div className="row pg-course-tags">
          {c.tags.map((t) => (
            <span key={t} className="badge">
              {t}
            </span>
          ))}
        </div>
      )}
      <div className="row pg-course-actions">
        <a className="btn" href={`/admin/courses/${c.id}`}>
          <Icon name="sparkles" />
          Apri
        </a>
        <a className="btn" href={pdfHref(c)} target="_blank" rel="noreferrer">
          <Icon name="download" />
          PDF
        </a>
      </div>
    </div>
  );
}

export default function CoursesBrowser() {
  const { data, error, loading } = useApi(() => get<Course[]>("/api/courses"));
  const states = useCourseStates();
  const [view, setView] = useLocalStorage<"grid" | "list">("lecta.courses.view", "grid");
  const [showNew, setShowNew] = useState(false);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    if (new URLSearchParams(location.search).get("new")) setShowNew(true);
  }, []);

  const stateOf = (id: number) => (states.data ?? []).find((s) => s.id === id);
  const courses = (data ?? []).filter((c) =>
    `${c.name} ${c.academic_year ?? ""} ${c.tags.join(" ")}`.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Materie</h1>
        </div>
        <button className="btn primary" onClick={() => setShowNew(true)}>
          <Icon name="plus" />
          Nuova materia
        </button>
      </div>
      <div className="pg-toolbar">
        <label className="pg-search">
          <Icon name="search" />
          <input type="search" placeholder="Filtra per nome, anno o tag" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filtra materie" />
        </label>
        <Seg<"grid" | "list">
          value={view}
          onChange={setView}
          options={[
            { key: "grid", label: <><Icon name="grid" />Griglia</> },
            { key: "list", label: <><Icon name="list" />Elenco</> },
          ]}
        />
      </div>
      <ErrorBox error={error} />
      {loading && !data ? (
        <Loading />
      ) : courses.length === 0 ? (
        <Empty icon="folder">
          {filter ? "Nessuna materia corrisponde al filtro." : "Nessuna materia. Creane una e prendi appunti nelle sue lezioni."}
        </Empty>
      ) : view === "grid" ? (
        <div className="pg-course-grid">
          {courses.map((c) => (
            <CourseCard key={c.id} c={c} st={stateOf(c.id)} />
          ))}
        </div>
      ) : (
        <div className="card flush">
          <div className="rows">
            {courses.map((c) => {
              const st = stateOf(c.id);
              const state = courseStateText(st);
              return (
                <a key={c.id} className="pg-course-row" href={`/admin/courses/${c.id}`}>
                  <Dot tone={COURSE_TONE[st?.status ?? "ok"] ?? ""} />
                  <div className="grow">
                    <div className="pg-row-title">{c.name}</div>
                    <div className="pg-row-sub">
                      {state ? <span className={state.tone === "danger" ? "text-danger" : ""}>{state.text} · </span> : null}
                      {metaLine(c, st)}
                    </div>
                  </div>
                  <span className="pg-row-flag hide-mobile">
                    <PublishedFlag published={c.published} />
                  </span>
                  <span className="muted small show-mobile">{c.published ? "Pubblicata" : "Privata"}</span>
                  <Icon name="chevron-right" className="faint" />
                </a>
              );
            })}
          </div>
        </div>
      )}
      {showNew && <NewCourseModal onClose={() => setShowNew(false)} onCreated={(c) => (location.href = `/admin/courses/${c.id}`)} />}
    </div>
  );
}
