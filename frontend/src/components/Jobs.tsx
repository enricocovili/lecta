import { useEffect, useState } from "react";
import { del, fmtDate, fmtMoney, get, post, sse } from "../lib/api";
import { Icon } from "./icons";
import { JobLogPanel } from "./JobLog";
import { jobKindLabel, jobStage, jobSteps, translateJobTitle, translateProgress } from "./jobtext";
import { JOB_TONE, JobPill, stageText } from "./pagekit";
import type { Job, JobLogEntry, TreeCourse } from "./types";
import { Confirm, Empty, ErrorBox, fmtWhen, Loading, Progress, toast, toastError, useApi, usePoll, type Tone } from "./ui";

const FINISHED = ["succeeded", "failed", "cancelled"];
const ACTIVE = ["queued", "running"];

type DeleteResult = { deleted: number[]; inbox_items: number; uploads: number };

function deletedText(r: DeleteResult): string {
  const extra = [
    r.inbox_items && `${r.inbox_items} ${r.inbox_items === 1 ? "elemento da smistare" : "elementi da smistare"}`,
    r.uploads && `${r.uploads} ${r.uploads === 1 ? "caricamento" : "caricamenti"}`,
  ].filter(Boolean);
  const n = r.deleted.length;
  return `${n === 1 ? "Eliminata 1 attività" : `Eliminate ${n} attività`}` + (extra.length ? `, ${extra.join(", ")}` : "");
}

const STATUS_PLURAL: Record<string, string> = { failed: "con errori", succeeded: "completate", cancelled: "annullate" };

/** Confirmation for deleting one job (`jobId`) or every job with a finished `status`. */
function DeleteJobs({
  jobId,
  status,
  onDone,
  onClose,
}: {
  jobId?: number;
  status?: string;
  onDone: () => void;
  onClose: () => void;
}) {
  const [purge, setPurge] = useState(false);
  return (
    <Confirm
      title={jobId ? `Eliminare l'attività #${jobId}?` : `Eliminare tutte le attività ${STATUS_PLURAL[status ?? ""] ?? status}?`}
      danger
      confirmLabel="Elimina"
      message={
        <div className="stack">
          <p style={{ margin: 0 }}>Il log e i progressi salvati vengono cancellati: non sarà più possibile riprovare.</p>
          <label className="check">
            <input type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} />
            Elimina anche le proposte non ancora decise e gli elementi da smistare che ha prodotto, e i file caricati (a meno che qualcosa sia già in una
            materia)
          </label>
        </div>
      }
      onConfirm={async () => {
        const r = jobId ? await del<DeleteResult>(`/api/jobs/${jobId}?purge=${purge}`) : await post<DeleteResult>("/api/jobs/delete", { status, purge });
        toast(deletedText(r));
        onDone();
      }}
      onClose={onClose}
    />
  );
}

/** Second line of an activity row: what happened / what is happening. */
function jobSubtitle(j: Job): { text: string; tone: Tone } {
  if (j.status === "failed") return { text: (j.error ?? "Non riuscita").split("\n")[0], tone: "danger" };
  if (j.status === "cancelled") return { text: `${jobKindLabel(j.kind)} annullata`, tone: "" };
  if (ACTIVE.includes(j.status)) {
    const t = j.status === "queued" ? "In attesa di un worker" : translateProgress(j.progress_text);
    return { text: t || jobKindLabel(j.kind), tone: "" };
  }
  const r = (j.result ?? {}) as Record<string, unknown>;
  const parts: string[] = [];
  if (j.kind === "publish") {
    parts.push("Compilato e pubblicato");
    if (typeof r.chapters === "number") parts.push(`${r.chapters} capitoli`);
  } else if (j.kind === "ingest" || j.kind === "inbox.assign") {
    const summary = r.summary as { items?: number } | undefined;
    const groups = (Array.isArray(r.groups) ? r.groups : []) as { type?: string }[];
    const written = groups.filter((g) => g.type && g.type !== "inbox").length;
    const inbox = groups.filter((g) => g.type === "inbox").length;
    parts.push(jobKindLabel(j.kind));
    if (summary?.items) parts.push(`${summary.items} ${summary.items === 1 ? "elemento analizzato" : "elementi analizzati"}`);
    if (written) parts.push(`${written} ${written === 1 ? "capitolo scritto" : "capitoli scritti"}`);
    if (inbox) parts.push(`${inbox} da smistare`);
  } else parts.push(jobKindLabel(j.kind));
  return { text: parts.join(" · "), tone: "" };
}

function ActivityRow({ j, course, onDelete }: { j: Job; course: string | null; onDelete: () => void }) {
  const sub = jobSubtitle(j);
  const tone = JOB_TONE[j.status] ?? "";
  const finished = FINISHED.includes(j.status);
  const icon = tone === "ok" ? "check" : tone === "danger" ? "alert-circle" : tone === "warn" ? "half" : "clock";
  return (
    <div className="pg-act-row" data-status={j.status}>
      <div className="pg-act-status">
        <span className={`status-ico show-mobile ${tone}`}>
          <Icon name={icon} />
        </span>
        <span className="hide-mobile">
          <JobPill status={j.status} />
        </span>
      </div>
      <div className="pg-act-main">
        <a className="pg-row-title" href={`/admin/jobs/${j.id}`}>
          {translateJobTitle(j.title)}
        </a>
        <div className={`pg-row-sub ellipsis ${sub.tone === "danger" ? "text-danger" : ""}`} title={sub.text}>
          {sub.text}
        </div>
        {course && (
          <div className="pg-row-sub show-mobile pg-act-course-m">
            <Icon name="folder" /> {course}
          </div>
        )}
        {!finished && <div className="pg-row-sub show-mobile">{stageText(j)}</div>}
      </div>
      <div className="pg-act-course hide-mobile">
        {course && (
          <>
            <Icon name="folder" />
            <span className="ellipsis">{course}</span>
          </>
        )}
      </div>
      <div className="pg-act-stage hide-mobile">
        <div className="pg-act-stage-label ellipsis">{stageText(j)}</div>
        <Progress value={finished ? 1 : j.progress} tone={tone} />
      </div>
      <span className="pg-time mono pg-act-time" title={fmtDate(j.created_at, true)}>
        {fmtWhen(j.finished_at ?? j.updated_at ?? j.created_at)}
      </span>
      <div className="pg-act-actions hide-mobile">
        <a className="btn" href={`/admin/jobs/${j.id}`}>
          Dettagli
        </a>
        {finished && (
          <button className="btn ghost icon sm" onClick={onDelete} title="Elimina questa attività" aria-label={`Elimina l'attività #${j.id}`}>
            <Icon name="trash" />
          </button>
        )}
      </div>
    </div>
  );
}

type Filter = "" | "failed" | "active" | "succeeded" | "cancelled";

export function JobsList() {
  const [filter, setFilter] = useState<Filter>("");
  const { data, error, loading, reload } = useApi(() => get<Job[]>(`/api/jobs?limit=200`), []);
  const [tree, setTree] = useState<TreeCourse[]>([]);
  const [deleting, setDeleting] = useState<{ jobId?: number; status?: string } | null>(null);
  useEffect(() => {
    get<TreeCourse[]>("/api/tree").then(setTree).catch(() => undefined);
  }, []);
  const all = data ?? [];
  const active = all.some((j) => ACTIVE.includes(j.status));
  usePoll(reload, active ? 3000 : 15000);
  const match = (j: Job, f: Filter) => (f === "" ? true : f === "active" ? ACTIVE.includes(j.status) : j.status === f);
  const count = (f: Filter) => all.filter((j) => match(j, f)).length;
  const shown = all.filter((j) => match(j, filter));
  const courseName = (id: number | null) => (id ? (tree.find((c) => c.id === id)?.name ?? null) : null);
  const chips: { key: Filter; label: string; tone?: Tone }[] = [
    { key: "", label: "Tutte" },
    { key: "failed", label: "Errori", tone: "danger" },
    { key: "active", label: "In lavorazione", tone: "warn" },
    { key: "succeeded", label: "Completate", tone: "ok" },
    ...(count("cancelled") > 0 || filter === "cancelled" ? [{ key: "cancelled" as Filter, label: "Annullate" }] : []),
  ];
  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Attività</h1>
          <div className="sub">Importazioni, pubblicazioni e smistamenti: cosa sta facendo Lecta e cosa è andato storto.</div>
        </div>
      </div>
      <div className="pg-toolbar">
        <div className="chips" role="tablist" aria-label="Filtra per stato">
          {chips.map((c) => (
            <button key={c.key} role="tab" aria-selected={filter === c.key} className={`chip ${filter === c.key ? "active" : ""}`} onClick={() => setFilter(c.key)}>
              {c.tone !== undefined && <span className={`dot ${c.tone}`} />}
              {c.label}
              <span className="n">{count(c.key)}</span>
            </button>
          ))}
        </div>
        {FINISHED.includes(filter) && shown.length > 0 && (
          <button className="btn danger sm" onClick={() => setDeleting({ status: filter })}>
            <Icon name="trash" />
            Elimina tutte
          </button>
        )}
      </div>
      {deleting && <DeleteJobs {...deleting} onDone={reload} onClose={() => setDeleting(null)} />}
      <ErrorBox error={error} />
      {loading && !data ? (
        <Loading />
      ) : shown.length === 0 ? (
        <Empty icon="activity">{filter ? "Nessuna attività con questo stato." : "Ancora nessuna attività. Carica del materiale per cominciare."}</Empty>
      ) : (
        <div className="card flush">
          <div className="rows">
            {shown.map((j) => (
              <ActivityRow key={j.id} j={j} course={courseName(j.course_id)} onDelete={() => setDeleting({ jobId: j.id })} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** The job's steps as a small three-part stepper ("Analisi e lettura › Generazione LaTeX › Collocazione"). */
function Stepper({ job }: { job: Job }) {
  const names = jobSteps(job.kind);
  if (names.length < 2) return null;
  const s = jobStage(job);
  const tone = JOB_TONE[job.status] ?? "";
  return (
    <ol className="pg-stepper">
      {names.map((n, i) => {
        const idx = i + 1;
        const state = job.status === "succeeded" || idx < s.step ? "done" : idx === s.step ? `current ${tone}` : "";
        return (
          <li key={n} className={state}>
            <span className="pg-step-dot">{idx < s.step || job.status === "succeeded" ? <Icon name="check" /> : idx}</span>
            <span className="pg-step-name">{n}</span>
          </li>
        );
      })}
    </ol>
  );
}

export function JobDetail({ jobId, children }: { jobId: number; children?: (job: Job) => React.ReactNode }) {
  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<JobLogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [course, setCourse] = useState<{ id: number; name: string } | null>(null);

  const load = async () => {
    try {
      const j = await get<Job>(`/api/jobs/${jobId}`);
      setJob(j);
      setLogs(j.logs ?? []);
      return j;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    }
  };

  useEffect(() => {
    let close: (() => void) | null = null;
    load().then((j) => {
      if (!j || FINISHED.includes(j.status)) return;
      close = sse(
        `/api/jobs/${jobId}/events`,
        {
          job: (d: Job) => setJob((prev) => ({ ...(prev ?? d), ...d })),
          log: (entry: JobLogEntry) => setLogs((xs) => (xs.some((x) => x.id === entry.id) ? xs : [...xs, entry])),
        },
        () => load(),
      );
    });
    return () => close?.();
  }, [jobId]);

  const courseId = job?.course_id ?? null;
  useEffect(() => {
    if (!courseId) return;
    get<TreeCourse[]>("/api/tree")
      .then((t) => {
        const c = t.find((x) => x.id === courseId);
        if (c) setCourse({ id: c.id, name: c.name });
      })
      .catch(() => undefined);
  }, [courseId]);

  if (error) return <ErrorBox error={error} />;
  if (!job) return <Loading />;
  const finished = FINISHED.includes(job.status);
  const tone = JOB_TONE[job.status] ?? "";
  const s = jobStage(job);

  const act = async (what: "cancel" | "retry" | "retry-scratch") => {
    try {
      if (what === "cancel") await post(`/api/jobs/${job.id}/cancel`);
      else await post(`/api/jobs/${job.id}/retry`, { from_scratch: what === "retry-scratch" });
      location.reload();
    } catch (e) {
      toastError(e);
    }
  };

  return (
    <div>
      <div className="page-head">
        <div className="grow" style={{ minWidth: 0 }}>
          <div className="row pg-head-pills">
            <JobPill status={job.status} size="sm" />
            <span className="badge">{jobKindLabel(job.kind)}</span>
            <span className="badge mono">#{job.id}</span>
          </div>
          <h1 className="pg-job-title">{translateJobTitle(job.title)}</h1>
          <div className="sub">
            {course && (
              <>
                <a href={`/admin/courses/${course.id}`} className="pg-sub-link">
                  <Icon name="folder" /> {course.name}
                </a>{" "}
                ·{" "}
              </>
            )}
            creata {fmtDate(job.created_at, true)}
            {job.attempts > 1 && <> · tentativo {job.attempts}</>}
          </div>
        </div>
        <div className="row">
          {!finished && (
            <button className="btn danger" onClick={() => act("cancel")} disabled={job.cancel_requested}>
              <Icon name="x" />
              {job.cancel_requested ? "Annullamento…" : "Annulla"}
            </button>
          )}
          {finished && (
            <>
              <button className="btn" onClick={() => act("retry")} title="Riprende riusando i passi già completati">
                <Icon name="refresh" />
                Riprova
              </button>
              <button className="btn" onClick={() => act("retry-scratch")} title="Ricomincia da zero">
                Riprova da capo
              </button>
              <button className="btn danger" onClick={() => setDeleting(true)}>
                <Icon name="trash" />
                Elimina
              </button>
            </>
          )}
        </div>
      </div>
      {deleting && <DeleteJobs jobId={job.id} onDone={() => location.assign("/admin/jobs")} onClose={() => setDeleting(false)} />}
      <div className="card pg-pad stack pg-job-progress">
        <Stepper job={job} />
        <div className="row between pg-nw" style={{ alignItems: "baseline" }}>
          <span className="pg-row-title">
            {s.label}
            {s.steps > 1 && job.status !== "succeeded" && <span className="muted" style={{ fontWeight: 400 }}> · {s.step} di {s.steps}</span>}
          </span>
          <span className="mono small muted">{Math.round((finished && job.status === "succeeded" ? 1 : job.progress) * 100)}%</span>
        </div>
        <Progress value={job.status === "succeeded" ? 1 : job.progress} tone={tone} />
        <div className="row between small" style={{ gap: ".5rem 1.5rem" }}>
          <span className="muted">{translateProgress(job.progress_text)}</span>
          <span className="muted mono tiny">
            {job.tokens_in + job.tokens_out > 0 && (
              <>
                {job.tokens_in.toLocaleString("it-IT")} token in / {job.tokens_out.toLocaleString("it-IT")} out ·{" "}
              </>
            )}
            {fmtMoney(job.cost_usd)}
          </span>
        </div>
        {job.error && <pre className="small pg-job-error">{job.error}</pre>}
      </div>
      {children?.(job)}
      <JobLogPanel jobId={job.id} liveLogs={logs} active={!finished} />
    </div>
  );
}
