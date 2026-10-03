import { useMemo, useState } from "react";
import { ApiError, get, post } from "../lib/api";
import { Icon } from "./icons";
import { resultText, type ImportResult } from "./IngestManifest";
import { jobStage, translateJobTitle, translateProgress } from "./jobtext";
import { COURSE_LABEL, COURSE_TONE } from "./pagekit";
import { Confirm, ErrorBox, fmtWhen, Loading, Progress, StatusPill, Switch, toast, toastError, useApi, usePoll, type Tone } from "./ui";

interface FirstError {
  file: string | null;
  line: number | null;
  message: string;
  chapter_id?: number;
  chapter_title?: string;
}

interface CourseState {
  id: number;
  name: string;
  slug: string;
  published: boolean;
  updated_at: string;
  chapter_count: number;
  status: "ok" | "working" | "error";
  reason: "compile" | "job" | null;
  active_job: { id: number; kind: string; title: string; status: string; progress: number; progress_text: string } | null;
  failed_job?: { id: number; title: string; error: string | null };
  last_build: { id: number; status: string; at: string; errors: number; first_error: FirstError | null } | null;
  sources: number;
  /** Latest AI review of the course (feedback on the whole document). */
  review?: { score: number; verdict: string; at: string; message_id: number; stale?: boolean } | null;
  ai_running?: boolean;
}

/** One turn of the assistant, from any course. */
interface AiTurn {
  id: number;
  session_id: number;
  course_id: number;
  course_name: string;
  status: string;
  at: string;
  text: string;
  files: number | unknown[];
  chapters: string[];
  undone: boolean;
  review_score: number | null;
  steps: number | unknown[];
  running: boolean;
}

interface ActivityJob {
  id: number;
  kind: string;
  title: string;
  status: string;
  progress: number;
  progress_text: string;
  error: string | null;
  course_id: number | null;
  course_name: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  upload: { via: string; file_count: number; names: string[]; images: number } | null;
  results: ImportResult[];
}

interface DashboardData {
  activity: ActivityJob[];
  ai_activity?: AiTurn[];
  courses: CourseState[];
  inbox_count: number;
}

interface Concepts {
  courses: { id: number; concept_count: number }[];
}

type Kind = "danger" | "warn" | "ok" | "";
type Filter = "all" | "danger" | "warn" | "ok";

/** One row of the ATTIVITÀ list, whatever it comes from (a job or a failed build). */
interface Row {
  key: string;
  tone: Kind;
  label: string;
  title: string;
  subtitle: string;
  course: { id: number; name: string } | null;
  stage: { label: string; value: number };
  when: string;
  at: string;
  action: { label: string; href: string; external?: boolean } | null;
}

const PILL_LABEL: Record<Kind, string> = { danger: "Errore", warn: "In lavorazione", ok: "Completato", "": "Annullato" };

function uploadSubtitle(j: ActivityJob): string {
  const u = j.upload;
  if (!u) return "";
  if (u.via === "lesson") return "Da una lezione con i tuoi appunti";
  if (u.via === "quick" && u.images > 0) return u.images === 1 ? "1 immagine caricata dal telefono" : `${u.images} immagini caricate dal telefono`;
  if (u.names.length === 1) return `Da ${u.names[0]}`;
  if (u.names.length > 1) return `Da ${u.names.slice(0, 2).join(", ")}${u.file_count > 2 ? ` e altri ${u.file_count - 2}` : ""}`;
  return `${u.file_count} file`;
}

function jobRow(j: ActivityJob, courses: Map<number, CourseState>): Row {
  const course = j.course_id ? { id: j.course_id, name: j.course_name ?? "" } : null;
  const at = j.finished_at ?? j.updated_at;
  const stage = jobStage(j);
  const base = { key: `job-${j.id}`, title: translateJobTitle(j.title) || "Attività", course, at, when: fmtWhen(at) };
  if (j.status === "failed") {
    return {
      ...base,
      tone: "danger",
      label: PILL_LABEL.danger,
      subtitle: j.error ?? "Non riuscita",
      stage: { label: `${stage.label} · ${stage.step} di ${stage.steps}`, value: 1 },
      action: { label: "Dettagli", href: `/admin/jobs/${j.id}` },
    };
  }
  if (["queued", "running"].includes(j.status)) {
    return {
      ...base,
      tone: "warn",
      label: PILL_LABEL.warn,
      subtitle: uploadSubtitle(j) || translateProgress(j.progress_text),
      stage: { label: j.status === "queued" ? `In coda · ${stage.step} di ${stage.steps}` : `${stage.label} · ${stage.step} di ${stage.steps}`, value: Math.max(0.04, j.progress) },
      when: j.status === "running" && Date.now() - new Date(j.updated_at).getTime() < 90_000 ? "ora" : fmtWhen(j.created_at),
      action: { label: "Dettagli", href: `/admin/jobs/${j.id}` },
    };
  }
  if (j.status === "cancelled") {
    return { ...base, tone: "", label: PILL_LABEL[""], subtitle: "Annullata", stage: { label: "Annullata", value: 0 }, action: { label: "Dettagli", href: `/admin/jobs/${j.id}` } };
  }
  // succeeded
  const done = { ...base, tone: "ok" as const, label: PILL_LABEL.ok };
  if (j.kind === "publish") {
    const c = j.course_id ? courses.get(j.course_id) : undefined;
    return {
      ...done,
      subtitle: "Compilato e pubblicato",
      stage: { label: "Pubblicato", value: 1 },
      action: c ? { label: "Apri PDF", href: c.published ? `/api/public/courses/${c.slug}.pdf` : `/api/courses/${c.id}/pdf`, external: true } : null,
    };
  }
  const results = j.results ?? [];
  const written = results.filter((r) => r.type && r.type !== "inbox");
  const inbox = results.filter((r) => r.type === "inbox");
  const broken = written.some((r) => r.compile === "error");
  if (written.length) {
    const first = written[0];
    return {
      ...done,
      tone: broken ? ("warn" as const) : done.tone,
      subtitle: results.map(resultText).join(" · ") + (broken ? " · da correggere" : ""),
      stage: { label: inbox.length ? "Scritto, in parte da smistare" : "Nel corso", value: 1 },
      action: first.course_id ? { label: "Apri", href: `/admin/courses/${first.course_id}${first.chapter_id ? `?chapter=${first.chapter_id}` : ""}` } : null,
    };
  }
  if (inbox.length) {
    return { ...done, subtitle: results.map(resultText).join(" · "), stage: { label: "Da smistare", value: 1 }, action: { label: "Smista", href: "/admin/inbox" } };
  }
  return {
    ...done,
    subtitle: uploadSubtitle(j) || "Completato",
    stage: { label: "Completato", value: 1 },
    action: j.course_id ? { label: "Apri", href: `/admin/courses/${j.course_id}` } : { label: "Dettagli", href: `/admin/jobs/${j.id}` },
  };
}

function buildRows(data: DashboardData): Row[] {
  const courses = new Map(data.courses.map((c) => [c.id, c]));
  const rows: Row[] = [];
  // A course whose latest compile failed shows up as an error even if no job is involved.
  for (const c of data.courses) {
    const b = c.last_build;
    if (c.reason !== "compile" || !b) continue;
    const fe = b.first_error;
    const where = fe?.line ? `alla riga ${fe.line}` : "";
    rows.push({
      key: `build-${b.id}`,
      tone: "danger",
      label: PILL_LABEL.danger,
      title: fe?.chapter_title ?? (fe?.file ? fe.file.split("/").pop()! : "Compilazione"),
      subtitle: `Compilazione fallita ${where}${fe?.message ? `: ${fe.message}` : ""}`.replace(/\s+:/, ":"),
      course: { id: c.id, name: c.name },
      stage: { label: `Compilazione PDF${b.errors > 1 ? ` · ${b.errors} errori` : ""}`, value: 1 },
      at: b.at,
      when: fmtWhen(b.at),
      action: {
        label: "Correggi",
        href: `/admin/courses/${c.id}?${fe?.chapter_id ? `chapter=${fe.chapter_id}&` : ""}chat=1&ask=${encodeURIComponent(`L'ultima compilazione è fallita${where ? ` ${where}` : ""}${fe?.message ? `: ${fe.message}` : ""}. Puoi correggere?`)}`,
      },
    });
  }
  for (const j of data.activity) rows.push(jobRow(j, courses));
  // Errors first, then work in progress, then the rest; newest first within each group.
  const rank: Record<Kind, number> = { danger: 0, warn: 1, ok: 2, "": 3 };
  return rows.sort((a, b) => rank[a.tone] - rank[b.tone] || new Date(b.at).getTime() - new Date(a.at).getTime());
}

function courseMeta(c: CourseState, concepts: number | undefined): string {
  let head: string;
  if (c.status === "error") head = c.reason === "compile" ? "Errore di compilazione" : "Importazione non riuscita";
  else if (c.status === "working" && c.active_job) head = `${translateJobTitle(c.active_job.title)} in corso`;
  else {
    const d = new Date(c.last_build?.at ?? c.updated_at);
    const today = d.toDateString() === new Date().toDateString();
    head = today
      ? `Aggiornata oggi alle ${d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}`
      : `Aggiornata il ${d.toLocaleDateString("it-IT", { day: "numeric", month: "short" })}`;
  }
  if (concepts === undefined) return head;
  return `${head} · ${concepts === 1 ? "1 concetto in comune" : `${concepts} concetti in comune`}`;
}

function pdfHref(c: CourseState) {
  return c.published ? `/api/public/courses/${c.slug}.pdf` : `/api/courses/${c.id}/pdf`;
}

/** Publishing is ON/OFF: while ON the site always shows the latest version (rebuilt automatically). */
function PublishSwitch({ course, onChanged }: { course: CourseState; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const updating = course.active_job?.kind === "publish";

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

function ActivityRow({ r }: { r: Row }) {
  return (
    <div className="home-act">
      <div className="home-act-status">
        <StatusPill tone={r.tone as Tone} label={r.label} />
      </div>
      <div className="home-act-main">
        <div className="home-act-title ellipsis">{r.title}</div>
        <div className={`home-act-sub ellipsis ${r.tone === "danger" ? "text-danger" : ""}`} title={r.subtitle}>
          {r.subtitle}
        </div>
      </div>
      <div className="home-act-course">
        {r.course && (
          <a href={`/admin/courses/${r.course.id}`} className="ellipsis">
            <Icon name="folder" />
            {r.course.name}
          </a>
        )}
      </div>
      <div className="home-act-stage">
        <span className="ellipsis">{r.stage.label}</span>
        <Progress value={r.stage.value} tone={(r.tone || "") as "" | "ok" | "warn" | "danger"} />
      </div>
      <div className="home-act-when mono">{r.when}</div>
      <div className="home-act-action">
        {r.action && (
          <a className="btn" href={r.action.href} {...(r.action.external ? { target: "_blank", rel: "noreferrer" } : {})}>
            {r.action.label}
          </a>
        )}
      </div>
    </div>
  );
}

function MobileActivityRow({ r }: { r: Row }) {
  const icon = r.tone === "danger" ? "alert-circle" : r.tone === "warn" ? "half" : r.tone === "ok" ? "check" : "clock";
  return (
    <a className="home-mact" href={r.action?.href ?? "#"} {...(r.action?.external ? { target: "_blank", rel: "noreferrer" } : {})}>
      <span className={`status-ico ${r.tone}`}>
        <Icon name={icon} />
      </span>
      <span className="home-mact-main">
        <span className="row between nowrap" style={{ alignItems: "baseline" }}>
          <strong className="ellipsis">{r.title}</strong>
          <span className="mono small muted nowrap">{r.when}</span>
        </span>
        {r.course && <span className="muted ellipsis">{r.course.name}</span>}
        <span className={`ellipsis ${r.tone === "danger" ? "text-danger" : "muted"}`}>{r.tone === "danger" ? r.subtitle : r.stage.label}</span>
      </span>
    </a>
  );
}

function count(x: number | unknown[] | undefined): number {
  return Array.isArray(x) ? x.length : (x ?? 0);
}

/** What the assistant did lately, across the courses. */
function AiFeed({ turns }: { turns: AiTurn[] }) {
  return (
    <div className="card flush rows home-ai">
      {turns.map((t) => {
        const files = count(t.files);
        const icon = t.running ? "loader" : t.undone ? "refresh" : t.status === "error" ? "alert-circle" : t.review_score != null ? "sparkles" : "check";
        return (
          <a key={t.id} className="home-ai-row" href={`/admin/courses/${t.course_id}?chat=1`}>
            <span className={`status-ico ${t.running ? "warn" : t.status === "error" ? "danger" : t.undone ? "" : "ok"}`}>
              <Icon name={icon} className={t.running ? "spin" : ""} />
            </span>
            <span className="home-ai-main">
              <span className="home-ai-text">{t.text || "Richiesta all'assistente"}</span>
              <span className="home-ai-sub">
                <strong>{t.course_name}</strong>
                {files > 0 && <> · {files === 1 ? "1 file modificato" : `${files} file modificati`}</>}
                {t.chapters.length > 0 && <> · {t.chapters.slice(0, 3).join(", ")}{t.chapters.length > 3 ? "…" : ""}</>}
              </span>
            </span>
            {t.review_score != null && <span className="badge accent">Revisione {t.review_score}/10</span>}
            {t.undone && <span className="badge">annullata</span>}
            {t.running && <span className="badge warn">in corso</span>}
            <span className="mono small muted nowrap">{fmtWhen(t.at)}</span>
          </a>
        );
      })}
    </div>
  );
}

function ReviewLine({ c }: { c: CourseState }) {
  const r = c.review;
  if (!r) {
    return (
      <a className="home-review none" href={`/admin/courses/${c.id}?chat=1&ask=${encodeURIComponent("Fai una revisione completa del corso.")}`}>
        <Icon name="sparkles" />
        Nessuna revisione: chiedi all'AI un giudizio sul corso
      </a>
    );
  }
  const tone = r.score >= 8 ? "ok" : r.score >= 6 ? "warn" : "danger";
  return (
    <a className={`home-review ${r.stale ? "stale" : ""}`} href={`/admin/courses/${c.id}?chat=1`} title={r.verdict}>
      <span className={`home-score ${tone}`}>{r.score}</span>
      <span className="home-review-text">
        <span className="home-review-verdict">{r.verdict}</span>
        <span className="tiny muted">
          Revisione AI · {fmtWhen(r.at)}
          {r.stale && " · il corso è cambiato da allora"}
        </span>
      </span>
    </a>
  );
}

export default function Dashboard() {
  const { data, error, loading, reload } = useApi(() => get<DashboardData>("/api/dashboard"));
  const concepts = useApi(() => get<Concepts>("/api/concepts").catch(() => null));
  const [filter, setFilter] = useState<Filter>("all");
  const rows = useMemo(() => (data ? buildRows(data) : []), [data]);
  const busy = rows.some((r) => r.tone === "warn") || !!data?.courses.some((c) => c.ai_running);
  usePoll(reload, busy ? 4000 : 20000);

  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const counts = { danger: 0, warn: 0, ok: 0 };
  for (const r of rows) if (r.tone && r.tone in counts) counts[r.tone as keyof typeof counts]++;
  const shown = rows.filter((r) => filter === "all" || r.tone === filter).slice(0, 8);
  const conceptCount = new Map((concepts.data?.courses ?? []).map((c) => [c.id, c.concept_count]));
  const aiTurns = (data.ai_activity ?? []).slice(0, 6);
  const changed = () => {
    reload();
    window.dispatchEvent(new Event("lecta:tree-changed"));
  };

  const chips: { key: Filter; label: string; n: number; dot?: string }[] = [
    { key: "all", label: "Tutte", n: rows.length },
    { key: "danger", label: "Errori", n: counts.danger, dot: "danger" },
    { key: "warn", label: "In lavorazione", n: counts.warn, dot: "warn" },
    { key: "ok", label: "Completate", n: counts.ok, dot: "ok" },
  ];

  return (
    <div className="home">
      {data.inbox_count > 0 && (
        <div className="home-notices">
          <a className="alert" href="/admin/inbox">
            <Icon name="inbox" />
            <span className="grow">
              {data.inbox_count === 1 ? "1 caricamento da smistare" : `${data.inbox_count} caricamenti da smistare`}: scegli in quale materia vanno.
            </span>
            <Icon name="chevron-right" />
          </a>
        </div>
      )}

      <div className="section-label" style={{ marginTop: 0 }}>
        <span>Materie</span>
        <a href="/admin/map" className="hide-mobile">
          Mappa collegamenti <Icon name="chevron-right" />
        </a>
      </div>

      {data.courses.length === 0 ? (
        <div className="empty">
          <Icon name="book" />
          <div>
            Nessuna materia. <a href="/admin/courses?new=1">Creane una</a> oppure <a href="/admin/upload">carica del materiale</a>.
          </div>
        </div>
      ) : (
        <>
          <div className="home-courses hide-mobile">
            {data.courses.map((c) => (
              <div key={c.id} className="card home-course">
                <div className="row between nowrap">
                  {c.ai_running ? <StatusPill tone="warn" label="L'AI sta lavorando" /> : <StatusPill tone={COURSE_TONE[c.status]} label={COURSE_LABEL[c.status]} />}
                  <PublishSwitch course={c} onChanged={changed} />
                </div>
                <h3 className="card-title">
                  <a href={`/admin/courses/${c.id}`}>{c.name}</a>
                </h3>
                <p className="home-course-meta">{courseMeta(c, conceptCount.get(c.id))}</p>
                <ReviewLine c={c} />
                <div className="row">
                  <a className="btn primary" href={`/admin/courses/${c.id}`}>
                    Apri
                  </a>
                  <a className="btn" href={pdfHref(c)} target="_blank" rel="noreferrer">
                    PDF
                  </a>
                  <a className="btn" href={`/api/courses/${c.id}/source.zip`} download title="Sorgente LaTeX (.zip)">
                    LaTeX
                  </a>
                </div>
              </div>
            ))}
          </div>
          <div className="card flush rows home-mcourses show-mobile">
            {data.courses.map((c) => (
              <a key={c.id} href={`/admin/courses/${c.id}`} className="home-mcourse">
                <span className={`dot ${c.ai_running ? "warn" : COURSE_TONE[c.status]}`} />
                <span className="grow">
                  {c.name}
                  {c.review && (
                    <span className="tiny muted block">
                      Revisione {c.review.score}/10 · {c.review.verdict}
                    </span>
                  )}
                </span>
                <span className="muted small">{c.published ? "Pubblica" : "Privata"}</span>
                <Icon name="chevron-right" />
              </a>
            ))}
          </div>
        </>
      )}

      <div className="section-label">
        <span>Ultime modifiche</span>
      </div>
      {aiTurns.length === 0 ? (
        <div className="empty">
          <Icon name="sparkles" />
          <div>L'assistente non ha ancora lavorato. Apri una materia e chiedigli qualcosa: modifiche e revisioni compaiono qui.</div>
        </div>
      ) : (
        <AiFeed turns={aiTurns} />
      )}

      {/* phone: three stat tiles instead of the filter chips */}
      <div className="home-stats">
        {chips.slice(1).map((c) => (
          <button key={c.key} className={`home-stat ${filter === c.key ? "active" : ""}`} onClick={() => setFilter(filter === c.key ? "all" : c.key)}>
            <span className="stat">
              <span className="label">
                <span className={`dot ${c.dot}`} />
                {c.key === "warn" ? "In corso" : c.label}
              </span>
              <span className="value">{c.n}</span>
            </span>
          </button>
        ))}
      </div>

      <div className="section-label">
        <span>Importazioni e lavori</span>
        <div className="chips hide-mobile" role="tablist" aria-label="Filtra le attività">
          {chips.map((c) => (
            <button key={c.key} role="tab" aria-selected={filter === c.key} className={`chip ${filter === c.key ? "active" : ""}`} onClick={() => setFilter(c.key)}>
              {c.dot && <span className={`dot ${c.dot}`} />}
              {c.label}
              <span className="n">{c.n}</span>
            </button>
          ))}
        </div>
        <a className="link show-mobile" href="/admin/jobs">
          Vedi tutte · {rows.length}
        </a>
      </div>

      {shown.length === 0 ? (
        <div className="empty">
          <Icon name="activity" />
          <div>
            {rows.length === 0 ? (
              <>
                Ancora nessun caricamento. <a href="/admin/upload">Carica slide, appunti o foto</a> e Lecta li trasformerà in un testo da studiare.
              </>
            ) : (
              "Niente in questa categoria."
            )}
          </div>
        </div>
      ) : (
        <>
          <div className="card flush rows home-acts hide-mobile">
            {shown.map((r) => (
              <ActivityRow key={r.key} r={r} />
            ))}
          </div>
          <div className="card flush rows home-macts show-mobile">
            {shown.slice(0, 5).map((r) => (
              <MobileActivityRow key={r.key} r={r} />
            ))}
          </div>
          {rows.length > shown.length && (
            <div className="home-more hide-mobile">
              <a href="/admin/jobs">Vedi tutte le attività →</a>
            </div>
          )}
        </>
      )}
    </div>
  );
}
