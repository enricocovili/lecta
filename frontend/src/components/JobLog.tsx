// Job diagnostics: grouped problems (what went wrong, how often, where) and the full,
// filterable log with structured context and links to the audited AI calls.
import { useEffect, useMemo, useState } from "react";
import { fmtMoney, get } from "../lib/api";
import { CallDetail } from "./AiUsage";
import type { JobLogEntry } from "./types";
import { Icon } from "./icons";
import { translateProgress } from "./jobtext";
import { ErrorBox, Loading, Seg, useApi, usePoll } from "./ui";

interface Problem {
  stage: string;
  kind: string;
  level: string;
  message: string;
  count: number;
  items: string[];
  audit_ids: number[];
  first_ts: string;
  last_ts: string;
  task: string | null;
  provider: string | null;
  model: string | null;
}

interface CallStats {
  task: string;
  target: string;
  ok: number;
  error: number;
  other: number;
  avg_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  error_kinds: Record<string, number>;
  sample_error_id: number | null;
}

interface TroubledFigure {
  id: number;
  key: string;
  item: string | null;
  status: string;
  origin: string;
  crop_url: string | null;
}

interface Problems {
  job: { id: number; status: string; error: string | null; at: string | null };
  problems: Problem[];
  calls: CallStats[];
  figures: { total: number; by_status: Record<string, number>; by_origin: Record<string, number>; troubled: TroubledFigure[] };
}

/** What a problem kind means, in plain words, and what usually helps. */
const KIND_HELP: Record<string, string> = {
  rate_limit: "Il provider ha rifiutato per troppe richieste. Abbassa la concorrenza o usa un modello/provider con limiti più alti.",
  quota: "Crediti esauriti o limite di spesa superato presso il provider.",
  auth: "La chiave API è stata rifiutata.",
  bad_request: "Il provider ha rifiutato la richiesta (spesso: immagine troppo grande, contesto troppo lungo o modello che non accetta immagini).",
  provider_error: "Il provider (o chi ospita il modello) ha avuto un errore.",
  timeout: "Nessuna risposta entro il tempo massimo della richiesta.",
  network: "Impossibile raggiungere il provider.",
  empty: "Il modello non ha restituito nulla. Senza token consumati, il modello non è nemmeno partito (di solito un errore a monte o un limite di richieste).",
  truncated: "La risposta ha raggiunto il limite di token in uscita prima di finire (i modelli di ragionamento possono consumarlo tutto pensando).",
  refused: "Il modello ha rifiutato o il filtro dei contenuti ha bloccato la risposta.",
  invalid_json: "Il modello ha risposto, ma non con il JSON richiesto.",
  compile_error: "Il capitolo scritto non compila (dopo la correzione automatica resta da correggere nel testo della materia).",
  split_unit: "Risposta troncata al limite di token: le pagine sono state rilette in due metà.",
  reply_unterminated: "La risposta non terminava con %%END: è stata usata così com'era.",
  pages_with_picture: "Alcune pagine sono state lette insieme alla loro immagine (scansioni, formule, annotazioni a mano).",
  images_ignored: "Immagini ignorate: loghi, decorazioni di intestazione o piè di pagina, ripetizioni.",
  unreadable_file: "Un file non si è potuto leggere.",
};

function levelClass(level: string) {
  return level === "error" ? "danger" : level === "warn" ? "warn" : "";
}

function ctxStr(v: unknown): string {
  return typeof v === "string" ? v : typeof v === "number" ? String(v) : "";
}

function Items({ items, onPick }: { items: string[]; onPick?: (item: string) => void }) {
  const [all, setAll] = useState(false);
  if (!items.length) return null;
  const shown = all ? items : items.slice(0, 6);
  return (
    <div className="row pg-log-items">
      {shown.map((i) => (
        <button key={i} className="badge mono" style={{ cursor: onPick ? "pointer" : "default", border: 0 }} onClick={() => onPick?.(i)} title="Mostra le sue righe di log">
          {i}
        </button>
      ))}
      {items.length > shown.length && (
        <button className="btn xs ghost" onClick={() => setAll(true)}>
          altri {items.length - shown.length}
        </button>
      )}
    </div>
  );
}

const FIG_STATUS: Record<string, string> = { used: "inserite", appended: "aggiunte in fondo", dropped: "non usate", pending: "in attesa" };
const FIG_ORIGIN: Record<string, string> = { image: "immagine", vector: "disegno vettoriale", drawing: "disegno dalla pagina", md_image: "immagine Markdown" };

function ProblemsView({
  data,
  onShow,
  onAudit,
}: {
  data: Problems;
  onShow: (f: { kind?: string; q?: string }) => void;
  onAudit: (id: number) => void;
}) {
  const { problems, calls, figures, job } = data;
  const figTotal = Object.entries(figures.by_status);
  return (
    <div className="stack">
      {job.status === "failed" && job.error && (
        <div className="alert danger small">
          <Icon name="alert-circle" />
          <span>
            <strong>Non riuscita{job.at ? ` durante “${translateProgress(job.at)}”` : ""}:</strong> {job.error.split("\n")[0]}
          </span>
        </div>
      )}
      {problems.length === 0 ? (
        <div className="pg-log-ok">
          <Icon name="check-circle" />
          Nessun avviso o errore registrato.
        </div>
      ) : (
        <div className="pg-problems">
          {problems.map((p, n) => (
            <div key={n} className={`pg-problem ${levelClass(p.level)}`}>
              <span className={`pg-problem-n mono ${levelClass(p.level)}`}>{p.count}×</span>
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="row pg-problem-tags">
                  <span className="badge">{p.stage}</span>
                  <span className={`badge ${levelClass(p.level)}`}>{p.kind}</span>
                  {p.task && <span className="faint mono tiny">{p.task}</span>}
                  {p.provider && (
                    <span className="faint tiny">
                      {p.provider}/{p.model}
                    </span>
                  )}
                </div>
                <div className="pg-problem-msg">{p.message}</div>
                {KIND_HELP[p.kind] && <div className="pg-row-sub">{KIND_HELP[p.kind]}</div>}
                <Items items={p.items} onPick={(i) => onShow({ q: i })} />
              </div>
              <div className="pg-problem-actions">
                <button className="btn sm" onClick={() => onShow({ kind: p.kind })}>
                  <Icon name="list" />
                  Mostra righe
                </button>
                {p.audit_ids[0] && (
                  <button className="btn sm ghost" onClick={() => onAudit(p.audit_ids[0])} title="Cosa è stato inviato e cosa è tornato">
                    <Icon name="shield" />
                    Audit #{p.audit_ids[0]}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {calls.length > 0 && (
        <>
          <div className="section-label">Chiamate IA</div>
          <div className="table-wrap pg-table-card">
            <table className="table">
              <thead>
                <tr>
                  <th>Compito</th>
                  <th>Provider / modello</th>
                  <th>OK</th>
                  <th>Fallite</th>
                  <th>Tempo medio</th>
                  <th>Token in / out</th>
                  <th>Costo</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((c) => (
                  <tr key={c.task + c.target}>
                    <td className="mono small">{c.task}</td>
                    <td className="small">{c.target}</td>
                    <td className="small mono">{c.ok}</td>
                    <td className="small">
                      {c.error > 0 ? (
                        <div className="row" style={{ gap: "0.3rem" }}>
                          <span className="badge danger">
                            {c.error} ({Math.round((100 * c.error) / Math.max(1, c.ok + c.error + c.other))}%)
                          </span>
                          {Object.entries(c.error_kinds).map(([k, v]) => (
                            <span key={k} className="badge" title={KIND_HELP[k] ?? ""}>
                              {k} {v}
                            </span>
                          ))}
                          {c.sample_error_id && (
                            <button className="btn xs ghost" onClick={() => onAudit(c.sample_error_id!)}>
                              esempio
                            </button>
                          )}
                        </div>
                      ) : (
                        <span className="mono">0</span>
                      )}
                    </td>
                    <td className="small nowrap mono">{(c.avg_ms / 1000).toFixed(1)} s</td>
                    <td className="small nowrap mono">
                      {c.tokens_in.toLocaleString("it-IT")} / {c.tokens_out.toLocaleString("it-IT")}
                    </td>
                    <td className="small mono">{fmtMoney(c.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {figures.total > 0 && (
        <>
          <div className="section-label">Immagini</div>
          <div className="row small">
            {figTotal.map(([st, n]) => (
              <span key={st} className={`badge ${st === "used" ? "ok" : st === "appended" ? "warn" : ""}`}>
                {n} {FIG_STATUS[st] ?? st}
              </span>
            ))}
            {Object.entries(figures.by_origin ?? {}).map(([o, n]) => (
              <span key={o} className="tiny muted">
                {n} {FIG_ORIGIN[o] ?? o}
              </span>
            ))}
            {figures.by_status.pending ? <span className="tiny muted">in attesa = l'attività si è fermata prima di scrivere gli appunti</span> : null}
          </div>
          {figures.troubled.length > 0 && (
            <div className="pg-problems">
              {figures.troubled.map((f) => (
                <div key={f.id} className="pg-problem pg-fig-trouble">
                  <div className="row pg-fig-thumbs">
                    {f.crop_url && <img src={f.crop_url} alt="immagine" />}
                  </div>
                  <div className="grow stack" style={{ gap: "0.25rem", minWidth: 0 }}>
                    <div className="row pg-problem-tags">
                      <strong className="mono small">{f.key}</strong>
                      <span className="badge">{FIG_STATUS[f.status] ?? f.status}</span>
                      <span className="faint tiny">{FIG_ORIGIN[f.origin] ?? f.origin}</span>
                      {f.item && <span className="muted tiny">{f.item}</span>}
                    </div>
                    <div>
                      <button className="btn sm ghost" onClick={() => onShow({ q: f.key })}>
                        <Icon name="list" />
                        Mostra righe
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

type LogFilter = { level: string; stage: string; kind: string; q: string };

function LogView({
  jobId,
  logs,
  filter,
  setFilter,
  onAudit,
}: {
  jobId: number;
  logs: JobLogEntry[];
  filter: LogFilter;
  setFilter: (f: LogFilter) => void;
  onAudit: (id: number) => void;
}) {
  const stages = useMemo(() => [...new Set(logs.map((l) => ctxStr(l.context?.stage)).filter(Boolean))].sort(), [logs]);
  const q = filter.q.trim().toLowerCase();
  const shown = logs.filter((l) => {
    if (filter.level === "warn" && l.level === "info") return false;
    if (filter.level === "error" && l.level !== "error") return false;
    if (filter.stage && ctxStr(l.context?.stage) !== filter.stage) return false;
    if (filter.kind && ctxStr(l.context?.kind) !== filter.kind) return false;
    if (q && !(l.message.toLowerCase().includes(q) || JSON.stringify(l.context ?? {}).toLowerCase().includes(q))) return false;
    return true;
  });
  const counts = { warn: logs.filter((l) => l.level === "warn").length, error: logs.filter((l) => l.level === "error").length };
  const levels: { key: string; label: string; n: number; tone?: string }[] = [
    { key: "", label: "Tutte", n: logs.length },
    { key: "warn", label: "Avvisi ed errori", n: counts.warn + counts.error, tone: "warn" },
    { key: "error", label: "Errori", n: counts.error, tone: "danger" },
  ];
  return (
    <div className="stack">
      <div className="chips" role="group" aria-label="Livello">
        {levels.map((l) => (
          <button key={l.key} className={`chip ${filter.level === l.key ? "active" : ""}`} onClick={() => setFilter({ ...filter, level: l.key })} aria-pressed={filter.level === l.key}>
            {l.tone && <span className={`dot ${l.tone}`} />}
            {l.label}
            <span className="n">{l.n}</span>
          </button>
        ))}
      </div>
      <div className="row pg-log-tools">
        <select value={filter.stage} onChange={(e) => setFilter({ ...filter, stage: e.target.value })} aria-label="Fase">
          <option value="">Tutte le fasi</option>
          {stages.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <label className="pg-search grow">
          <Icon name="search" />
          <input type="search" placeholder="Cerca nei messaggi, pagine, figure…" value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} aria-label="Cerca nel log" />
        </label>
        {filter.kind && (
          <button className="chip active" onClick={() => setFilter({ ...filter, kind: "" })} title="Togli questo filtro">
            tipo: {filter.kind} <Icon name="x" />
          </button>
        )}
        {(filter.level || filter.stage || filter.kind || filter.q) && (
          <button className="btn sm ghost" onClick={() => setFilter({ level: "", stage: "", kind: "", q: "" })}>
            Azzera filtri
          </button>
        )}
        <a className="btn sm" href={`/api/jobs/${jobId}/logs.txt`} download>
          <Icon name="download" />
          Scarica
        </a>
      </div>
      <div className="tiny muted mono">{shown.length === logs.length ? `${logs.length} righe` : `${shown.length} di ${logs.length} righe`}</div>
      <div className="pg-log">
        {shown.length === 0 && <div className="faint">Nessuna riga.</div>}
        {shown.map((l) => {
          const c = l.context ?? {};
          const auditId = typeof c.audit_id === "number" ? c.audit_id : null;
          const chips = (["stage", "kind", "figure", "attempt"] as const)
            .map((k) => (c[k] !== undefined && c[k] !== null ? `${k === "attempt" ? "tent. " : ""}${String(c[k])}` : ""))
            .filter(Boolean);
          return (
            <div key={l.id} className={`pg-log-line ${l.level === "error" ? "error" : l.level === "warn" ? "warn" : ""}`}>
              <span className="pg-log-ts">{new Date(l.ts).toLocaleTimeString("it-IT")}</span>
              <span className="pg-log-msg">
                {l.message}
                {chips.length > 0 && <span className="pg-log-ctx"> [{chips.join(" · ")}]</span>}
                {auditId && (
                  <>
                    {" "}
                    <a
                      href="#"
                      onClick={(e) => {
                        e.preventDefault();
                        onAudit(auditId);
                      }}
                    >
                      audit #{auditId}
                    </a>
                  </>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function JobLogPanel({ jobId, liveLogs, active }: { jobId: number; liveLogs: JobLogEntry[]; active: boolean }) {
  const [tab, setTab] = useState<"problems" | "log">("problems");
  const [filter, setFilter] = useState<LogFilter>({ level: "", stage: "", kind: "", q: "" });
  const [audit, setAudit] = useState<number | null>(null);
  const [all, setAll] = useState<JobLogEntry[] | null>(null);
  const problems = useApi(() => get<Problems>(`/api/jobs/${jobId}/problems`), [jobId]);
  usePoll(problems.reload, 5000, active);
  useEffect(() => {
    if (!active) problems.reload(); // final state once the job ends
  }, [active, problems.reload]);

  // The job detail only carries the latest lines; load the whole log once, then merge live lines.
  useEffect(() => {
    get<JobLogEntry[]>(`/api/jobs/${jobId}/logs?limit=10000`)
      .then(setAll)
      .catch(() => setAll(null));
  }, [jobId]);
  const logs = useMemo(() => {
    if (!all) return liveLogs;
    const last = all.length ? all[all.length - 1].id : 0;
    return [...all, ...liveLogs.filter((l) => l.id > last)];
  }, [all, liveLogs]);

  const nProblems = problems.data?.problems.reduce((n, p) => n + p.count, 0) ?? 0;
  const show = (f: { kind?: string; q?: string }) => {
    setFilter({ level: "", stage: "", kind: f.kind ?? "", q: f.q ?? "" });
    setTab("log");
  };
  return (
    <>
      <div className="section-label">
        <span>Diagnostica</span>
        <Seg<"problems" | "log">
          size="sm"
          value={tab}
          onChange={setTab}
          options={[
            { key: "problems", label: <>Problemi{nProblems ? <span className="pg-tab-n">{nProblems}</span> : null}</> },
            { key: "log", label: <>Log <span className="pg-tab-n">{logs.length}</span></> },
          ]}
        />
      </div>
      <div className="card pg-pad pg-joblog">
        {tab === "problems" ? (
          problems.error ? (
            <ErrorBox error={problems.error} />
          ) : !problems.data ? (
            <Loading />
          ) : (
            <ProblemsView data={problems.data} onShow={show} onAudit={setAudit} />
          )
        ) : (
          <LogView jobId={jobId} logs={logs} filter={filter} setFilter={setFilter} onAudit={setAudit} />
        )}
      </div>
      {audit && <CallDetail id={audit} onClose={() => setAudit(null)} />}
    </>
  );
}
