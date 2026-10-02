// AI calls and their costs (Settings → AI e costi). Only metadata is logged, never what was sent.
import { useMemo, useState } from "react";
import { fmtDate, fmtMoney, get } from "../lib/api";
import { Icon } from "./icons";
import { Empty, ErrorBox, fmtWhen, Loading, Modal, Seg, useApi, type Tone } from "./ui";

interface Row {
  id: number;
  ts: string;
  provider: string;
  provider_type: string;
  model: string;
  role: string | null;
  task: string | null;
  request_key: string | null;
  status: string;
  error: string | null;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  duration_ms: number;
  job_id: number | null;
  chat_session_id: number | null;
}

const STATUS_IT: Record<string, { tone: Tone; label: string }> = {
  ok: { tone: "ok", label: "Inviata" },
  error: { tone: "danger", label: "Errore" },
  sending: { tone: "warn", label: "In invio" },
};
const statusOf = (s: string) => STATUS_IT[s] ?? { tone: "" as Tone, label: s };

const fmtTok = (n: number) => n.toLocaleString("it-IT");

function Fact({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="aud-fact">
      <dt>{k}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** One AI call: when, which provider/model, what for, tokens, cost, duration, error. */
export function CallDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, loading } = useApi(() => get<Row>(`/api/audit/${id}`), [id]);
  return (
    <Modal title={`Richiesta #${id}`} onClose={onClose} wide>
      {loading || !data ? (
        <Loading />
      ) : (
        <div className="aud-detail">
          <dl className="aud-facts">
            <Fact k="Quando">{fmtDate(data.ts, true)}</Fact>
            <Fact k="Provider">{data.provider}</Fact>
            <Fact k="Modello">
              <span className="mono">{data.model}</span>
            </Fact>
            <Fact k="Attività">
              <span className="mono small">{data.task ?? "—"}</span>
              {data.job_id && (
                <>
                  {" "}
                  · <a href={`/admin/jobs/${data.job_id}`}>job #{data.job_id}</a>
                </>
              )}
            </Fact>
            <Fact k="Token (in / out)">
              <span className="mono">
                {fmtTok(data.tokens_in)} / {fmtTok(data.tokens_out)}
              </span>
            </Fact>
            <Fact k="Costo">
              <span className="mono">{fmtMoney(data.cost_usd)}</span>
            </Fact>
            <Fact k="Durata">
              <span className="mono">{(data.duration_ms / 1000).toFixed(1)} s</span>
            </Fact>
          </dl>
          {data.error && (
            <div className="alert danger small">
              <Icon name="alert-circle" />
              <span>{data.error}</span>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

type StatusFilter = "all" | "ok" | "error";

function RequestsTab({ rows }: { rows: Row[] }) {
  const [status, setStatus] = useState<StatusFilter>("all");
  const [provider, setProvider] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const providers = useMemo(() => [...new Set(rows.map((r) => r.provider))].sort(), [rows]);
  const shown = rows.filter(
    (r) =>
      (status === "all" || r.status === status) &&
      (!provider || r.provider === provider) &&
      (!q || `${r.task ?? ""} ${r.model} ${r.job_id ?? ""}`.toLowerCase().includes(q.toLowerCase())),
  );
  const nErr = rows.filter((r) => r.status === "error").length;
  const nOk = rows.filter((r) => r.status === "ok").length;
  const cost = shown.reduce((a, r) => a + r.cost_usd, 0);
  const tokens = shown.reduce((a, r) => a + r.tokens_in + r.tokens_out, 0);

  return (
    <>
      <div className="aud-stats">
        <div className="card stat">
          <span className="label">Richieste</span>
          <span className="value">{fmtTok(shown.length)}</span>
        </div>
        <div className="card stat">
          <span className="label">Token</span>
          <span className="value">{fmtTok(tokens)}</span>
        </div>
        <div className="card stat">
          <span className="label">Costo</span>
          <span className="value">{fmtMoney(cost)}</span>
        </div>
      </div>

      <div className="aud-filters">
        <div className="chips">
          {(
            [
              ["all", "Tutte", rows.length],
              ["ok", "Inviate", nOk],
              ["error", "Errori", nErr],
            ] as const
          ).map(([k, label, n]) => (
            <button key={k} type="button" className={`chip ${status === k ? "active" : ""}`} aria-pressed={status === k} onClick={() => setStatus(k)}>
              {k === "error" && <span className="dot danger" />}
              {k === "ok" && <span className="dot ok" />}
              {label} <span className="n">{n}</span>
            </button>
          ))}
        </div>
        <div className="aud-filters-right">
          <select aria-label="Provider" value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="">Tutti i provider</option>
            {providers.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <input type="search" aria-label="Filtra per attività, modello o job" placeholder="Attività, modello, job…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      {shown.length === 0 ? (
        <Empty icon="history">Nessuna richiesta {rows.length ? "con questi filtri" : "registrata finora"}.</Empty>
      ) : (
        <div className="card flush aud-table">
          <div className="aud-row aud-row-head" aria-hidden="true">
            <span>Quando</span>
            <span>Provider e modello</span>
            <span>Attività</span>
            <span>Durata</span>
            <span className="num">Token</span>
            <span className="num">Costo</span>
            <span>Stato</span>
          </div>
          {shown.map((r) => {
            const st = statusOf(r.status);
            return (
              <button key={r.id} type="button" className="aud-row" onClick={() => setOpen(r.id)} aria-label={`Dettagli richiesta #${r.id}`}>
                <span className="aud-when mono" title={fmtDate(r.ts, true)}>
                  {fmtWhen(r.ts)}
                </span>
                <span className="aud-prov">
                  <span className="aud-prov-name">
                    {r.provider}
                  </span>
                  <span className="mono tiny muted ellipsis">{r.model}</span>
                </span>
                <span className="aud-task">
                  <span className="mono small ellipsis">{r.task ?? "—"}</span>
                  {r.job_id && <span className="tiny muted">job #{r.job_id}</span>}
                </span>
                <span className="small muted aud-auth mono">{(r.duration_ms / 1000).toFixed(1)} s</span>
                <span className="mono small num aud-tok">
                  {fmtTok(r.tokens_in)} / {fmtTok(r.tokens_out)}
                </span>
                <span className="mono small num aud-cost">{fmtMoney(r.cost_usd)}</span>
                <span className="aud-st">
                  <span className={`badge ${st.tone}`}>{st.label}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
      {open && <CallDetail id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

export default function AiUsage() {
  const [tab, setTab] = useState<"log" | "costs">("costs");
  const log = useApi(() => get<Row[]>("/api/audit?limit=500"));
  const costs = useApi(() =>
    get<{
      months: { month: string; provider: string; tokens_in: number; tokens_out: number; cost_usd: number; requests: number }[];
      jobs: { id: number; title: string; tokens_in: number; tokens_out: number; cost_usd: number; created_at: string }[];
    }>("/api/costs"),
  );
  const monthTotal = (costs.data?.months ?? []).reduce((a, m) => a + m.cost_usd, 0);
  return (
    <div className="aud-page">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: ".5rem" }}>
        <div className="small muted">Ogni chiamata a un provider AI: quando, quale modello, per cosa, token e costo. Il contenuto inviato non viene conservato.</div>
        <Seg<"log" | "costs">
          size="sm"
          value={tab}
          onChange={setTab}
          options={[
            { key: "costs", label: "Costi" },
            { key: "log", label: "Chiamate" },
          ]}
        />
      </div>
      {tab === "log" && (
        <>
          <ErrorBox error={log.error} />
          {log.loading && !log.data ? <Loading /> : <RequestsTab rows={log.data ?? []} />}
        </>
      )}
      {tab === "costs" && (
        <>
          <ErrorBox error={costs.error} />
          {costs.loading && !costs.data && <Loading />}
          {costs.data && (
            <div className="aud-costs">
              <section>
                <div className="section-label">
                  <span>Per mese</span>
                  <span className="mono aud-total">{fmtMoney(monthTotal)}</span>
                </div>
                {costs.data.months.length === 0 ? (
                  <Empty icon="history">Ancora nessun costo.</Empty>
                ) : (
                  <div className="card flush rows">
                    {costs.data.months.map((m) => (
                      <div key={m.month + m.provider} className="aud-cost-row">
                        <span className="grow">
                          <strong className="aud-month">{new Date(m.month).toLocaleDateString("it-IT", { year: "numeric", month: "long" })}</strong>
                          <span className="small muted">
                            {m.provider} · {m.requests} {m.requests === 1 ? "richiesta" : "richieste"} · {fmtTok(m.tokens_in + m.tokens_out)} token
                          </span>
                        </span>
                        <span className="mono">{fmtMoney(m.cost_usd)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </section>
              <section>
                <div className="section-label">Per job</div>
                {costs.data.jobs.length === 0 ? (
                  <Empty icon="activity">Nessun job con costi.</Empty>
                ) : (
                  <div className="card flush rows">
                    {costs.data.jobs.map((j) => (
                      <a key={j.id} className="aud-cost-row link" href={`/admin/jobs/${j.id}`}>
                        <span className="grow" style={{ minWidth: 0 }}>
                          <strong className="ellipsis">{j.title}</strong>
                          <span className="small muted">
                            <span className="mono">#{j.id}</span> · {fmtWhen(j.created_at)} · {fmtTok(j.tokens_in + j.tokens_out)} token
                          </span>
                        </span>
                        <span className="mono">{fmtMoney(j.cost_usd)}</span>
                      </a>
                    ))}
                  </div>
                )}
              </section>
            </div>
          )}
        </>
      )}
    </div>
  );
}
