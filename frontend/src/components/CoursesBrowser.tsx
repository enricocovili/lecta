// The home page: the user's subjects, each with its state and whether it is published (decision 134).
import { useEffect, useState } from "react";
import { get, post } from "../lib/api";
import { courseText } from "../lib/links";
import { canCreate, isAdmin, otherOwner, type Role } from "../lib/roles";
import { Icon } from "./icons";
import { COURSE_TONE, CoursePill, courseStateText, fmtUpdated, OpenNotes, openNotesText, PublishedFlag, useCourseStates, type CourseState } from "./pagekit";
import type { Course } from "./types";
import { Confirm, Dot, Empty, ErrorBox, Loading, Modal, Seg, Switch, toast, toastError, useApi, useLocalStorage, usePoll } from "./ui";

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

/** Where «Correggi» leads when the latest compile failed: the text at the broken chapter, the assistant asked to fix it. */
function fixHref(c: Course, st: CourseState | undefined): string | null {
  if (st?.status !== "error" || st.reason !== "compile" || !c.can_write) return null;
  const fe = st.last_build?.first_error;
  const where = fe?.line ? ` alla riga ${fe.line}` : "";
  return courseText(c.id, { chapter: fe?.chapter_id, ask: `L'ultima compilazione è fallita${where}${fe?.message ? `: ${fe.message}` : ""}. Puoi correggere?` });
}

/** Publishing is ON/OFF: while ON the site always shows the latest version (rebuilt automatically). */
function PublishSwitch({ course, updating, onChanged }: { course: Course; updating: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const publish = async () => {
    setBusy(true);
    try {
      await post(`/api/courses/${course.id}/publish`);
      toast("La materia è pubblica: il PDF si sta preparando");
      onChanged();
    } catch (e) {
      toastError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Switch
        label={course.published ? (updating ? "Aggiornamento…" : "Pubblicata") : "Privata"}
        checked={course.published}
        disabled={busy}
        onChange={(v) => (v ? publish() : setConfirm(true))}
      />
      {confirm && (
        <Confirm
          title="Rendere privata?"
          message={
            <>
              <strong>{course.name}</strong> sparirà dal sito pubblico e il PDF non sarà più scaricabile. Puoi ripubblicarla quando vuoi.
            </>
          }
          confirmLabel="Rendi privata"
          danger
          onConfirm={async () => {
            await post(`/api/courses/${course.id}/unpublish`);
            onChanged();
          }}
          onClose={() => setConfirm(false)}
        />
      )}
    </>
  );
}

function CourseCard({ c, st, owner, admin, onChanged }: { c: Course; st: CourseState | undefined; owner: string | null; admin: boolean; onChanged: () => void }) {
  const state = courseStateText(st);
  const fix = fixHref(c, st);
  return (
    <div className="card pg-course-card">
      <div className="row between pg-nw">
        <CoursePill status={st?.ai_running ? "working" : st?.status} />
        {admin ? <PublishSwitch course={c} updating={st?.active_job?.kind === "publish"} onChanged={onChanged} /> : <PublishedFlag published={c.published} />}
      </div>
      <a className="card-link pg-course-name" href={`/admin/courses/${c.id}`}>
        {c.name}
      </a>
      <div className="pg-course-meta">
        {owner && <span data-testid="course-owner">di {owner} · </span>}
        {state ? (
          <>
            <span className={state.tone === "danger" ? "text-danger" : ""}>{state.text}</span>
            <span> · {chapterCount(c, st)}</span>
          </>
        ) : (
          metaLine(c, st)
        )}
      </div>
      <OpenNotes course={c} />
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
        {fix && (
          <a className="btn primary" href={fix}>
            Correggi
          </a>
        )}
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

export default function CoursesBrowser({ role, userId }: { role?: Role; userId?: number }) {
  const { data, error, loading, reload } = useApi(() => get<Course[]>("/api/courses"));
  const states = useCourseStates();
  // While something runs (an import, a compile, the assistant) the cards follow it.
  const busy = (states.data ?? []).some((s) => s.status === "working" || s.ai_running);
  usePoll(states.reload, busy ? 4000 : 20000);
  const [view, setView] = useLocalStorage<"grid" | "list">("lecta.courses.view", "grid");
  const [showNew, setShowNew] = useState(false);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    if (canCreate(role) && new URLSearchParams(location.search).get("new")) setShowNew(true);
  }, []);

  const stateOf = (id: number) => (states.data ?? []).find((s) => s.id === id);
  const changed = () => {
    void reload();
    void states.reload();
    window.dispatchEvent(new Event("lecta:tree-changed"));
  };
  const courses = (data ?? []).filter((c) =>
    `${c.name} ${c.academic_year ?? ""} ${c.tags.join(" ")}`.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Materie</h1>
        </div>
        {canCreate(role) && (
          <div className="row">
            <button className="btn" onClick={() => setShowNew(true)}>
              <Icon name="plus" />
              Nuova materia
            </button>
            <a className="btn primary" href="/admin/lessons?new=1">
              <Icon name="notebook" />
              Prendi appunti a lezione
            </a>
          </div>
        )}
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
          {filter ? "Nessuna materia corrisponde al filtro." : canCreate(role) ? "Nessuna materia. Creane una e prendi appunti nelle sue lezioni." : "Nessuna materia."}
        </Empty>
      ) : view === "grid" ? (
        <div className="pg-course-grid">
          {courses.map((c) => (
            <CourseCard key={c.id} c={c} st={stateOf(c.id)} owner={otherOwner(c, userId)} admin={isAdmin(role)} onChanged={changed} />
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
                  <Dot tone={COURSE_TONE[st?.ai_running ? "working" : (st?.status ?? "ok")] ?? ""} />
                  <div className="grow">
                    <div className="pg-row-title">{c.name}</div>
                    <div className="pg-row-sub">
                      {otherOwner(c, userId) && <span data-testid="course-owner">di {otherOwner(c, userId)} · </span>}
                      {state ? <span className={state.tone === "danger" ? "text-danger" : ""}>{state.text} · </span> : null}
                      {metaLine(c, st)}
                    </div>
                  </div>
                  {!!c.open_notes && <span className="pill sm warn">{openNotesText(c.open_notes)}</span>}
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
