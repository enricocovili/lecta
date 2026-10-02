import { useState } from "react";
import { del, fmtDate, get, patch, post } from "../../lib/api";
import { Icon } from "../icons";
import { Confirm, Empty, Loading, Modal, toast, toastError, useApi } from "../ui";
import { ToggleRow } from "./common";

interface ModelInfo {
  id: string;
  label?: string | null;
  input_per_mtok: number;
  output_per_mtok: number;
  vision: boolean;
}

export interface ProviderRow {
  id: number;
  name: string;
  type: string;
  type_label: string;
  base_url: string | null;
  default_base_url: string | null;
  has_api_key: boolean;
  api_key_hint: string | null;
  models: ModelInfo[];
  options: Record<string, unknown>;
  enabled: boolean;
  usable: boolean;
  unusable_reason: string | null;
  last_test: { ok: boolean; detail: string; at: string; ms: number } | null;
}

interface ProviderType {
  type: string;
  label: string;
  default_base_url: string;
  needs_key: boolean;
}

function ProviderForm({ initial, types, onClose, onSaved }: { initial?: ProviderRow; types: ProviderType[]; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: initial?.name ?? "",
    type: initial?.type ?? "anthropic",
    base_url: initial?.base_url ?? "",
    api_key: "",
    enabled: initial?.enabled ?? true,
    options: JSON.stringify(initial?.options ?? {}, null, 0),
  });
  const [models, setModels] = useState<ModelInfo[]>(initial?.models ?? []);
  const [clearKey, setClearKey] = useState(false);
  const t = types.find((x) => x.type === form.type);

  const save = async () => {
    let options: Record<string, unknown> = {};
    try {
      options = form.options.trim() ? JSON.parse(form.options) : {};
    } catch {
      return toast("Le opzioni devono essere JSON valido", "error");
    }
    const body = {
      name: form.name,
      base_url: form.base_url || null,
      enabled: form.enabled,
      options,
      models,
      ...(form.api_key ? { api_key: form.api_key } : {}),
      ...(clearKey ? { clear_api_key: true } : {}),
    };
    try {
      if (initial) await patch(`/api/providers/${initial.id}`, body);
      else await post("/api/providers", { ...body, type: form.type });
      toast(initial ? "Provider aggiornato" : "Provider aggiunto");
      onSaved();
      onClose();
    } catch (e) {
      toastError(e);
    }
  };

  const setModel = (i: number, m: Partial<ModelInfo>) => setModels(models.map((x, j) => (j === i ? { ...x, ...m } : x)));

  return (
    <Modal
      title={initial ? `Modifica ${initial.name}` : "Aggiungi provider"}
      onClose={onClose}
      wide
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Annulla
          </button>
          <button className="btn primary" onClick={save} disabled={!form.name}>
            Salva
          </button>
        </>
      }
    >
      <div className="set-form">
        <div className="form-grid">
          <label className="field">
            Nome
            <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Anthropic" />
          </label>
          <label className="field">
            Tipo
            <select value={form.type} disabled={!!initial} onChange={(e) => setForm({ ...form, type: e.target.value })}>
              {types.map((x) => (
                <option key={x.type} value={x.type}>
                  {x.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            URL base <span className="hint">predefinito: {t?.default_base_url || "—"}</span>
            <input type="url" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder={t?.default_base_url} />
          </label>
          <label className="field">
            Chiave API{" "}
            <span className="hint">
              {initial?.has_api_key ? `salvata (${initial.api_key_hint ?? "impostata"}); lascia vuoto per mantenerla` : t?.needs_key === false ? "non necessaria" : "cifrata sul server"}
            </span>
            <input type="password" autoComplete="off" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
          </label>
        </div>
        {initial?.has_api_key && (
          <label className="check small">
            <input type="checkbox" checked={clearKey} onChange={(e) => setClearKey(e.target.checked)} /> Rimuovi la chiave API salvata
          </label>
        )}

        <div className="set-toggles">
          <ToggleRow title="Attivo" hint="Un provider disattivato resta salvato ma non viene usato." checked={form.enabled} onChange={(v) => setForm({ ...form, enabled: v })} />
        </div>

        <div>
          <div className="section-label" style={{ margin: "0 0 .5rem" }}>
            <span>Modelli e prezzi</span>
            <button
              type="button"
              className="btn sm"
              onClick={() => setModels([...models, { id: "", label: "", input_per_mtok: 0, output_per_mtok: 0, vision: true }])}
            >
              <Icon name="plus" />
              Aggiungi modello
            </button>
          </div>
          <p className="set-help">Prezzi in USD per milione di token, usati per le stime dei costi. “Aggiorna modelli” nella scheda del provider aggiunge i modelli disponibili.</p>
          {models.length === 0 ? (
            <div className="set-help faint">Nessun modello.</div>
          ) : (
            <div className="set-models">
              <div className="set-models-head" aria-hidden="true">
                <span>ID modello</span>
                <span>Etichetta</span>
                <span>$ / M input</span>
                <span>$ / M output</span>
                <span>Visione</span>
                <span />
              </div>
              {models.map((m, i) => (
                <div key={i} className="set-models-row">
                  <input type="text" aria-label="ID modello" placeholder="ID modello" className="mono" value={m.id} onChange={(e) => setModel(i, { id: e.target.value })} />
                  <input type="text" aria-label="Etichetta" placeholder="Etichetta" value={m.label ?? ""} onChange={(e) => setModel(i, { label: e.target.value })} />
                  <input
                    type="number"
                    aria-label="$ / M input"
                    step="0.01"
                    min={0}
                    value={m.input_per_mtok}
                    onChange={(e) => setModel(i, { input_per_mtok: Number(e.target.value) })}
                  />
                  <input
                    type="number"
                    aria-label="$ / M output"
                    step="0.01"
                    min={0}
                    value={m.output_per_mtok}
                    onChange={(e) => setModel(i, { output_per_mtok: Number(e.target.value) })}
                  />
                  <label className="check small set-models-vision">
                    <input type="checkbox" checked={m.vision} onChange={(e) => setModel(i, { vision: e.target.checked })} />
                    <span className="show-mobile">Visione</span>
                  </label>
                  <button type="button" className="btn icon sm ghost danger" aria-label="Rimuovi modello" onClick={() => setModels(models.filter((_, j) => j !== i))}>
                    <Icon name="trash" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
        <details className="set-details">
          <summary>Opzioni avanzate (JSON)</summary>
          <p className="set-help">
            Compatibile OpenAI: <code>{'{"headers": {"HTTP-Referer": "…"}, "json_mode": false, "max_tokens_field": "max_tokens"}'}</code>
          </p>
          <textarea rows={3} aria-label="Opzioni avanzate (JSON)" value={form.options} onChange={(e) => setForm({ ...form, options: e.target.value })} />
        </details>
      </div>
    </Modal>
  );
}

function ProviderCard({ p, onEdit, onDelete, reload }: { p: ProviderRow; onEdit: () => void; onDelete: () => void; reload: () => void }) {
  const [busy, setBusy] = useState<"" | "test" | "fetch">("");
  const test = async () => {
    setBusy("test");
    try {
      const r = await post<{ ok: boolean; detail: string; ms: number }>(`/api/providers/${p.id}/test`);
      toast(r.ok ? `Connessione OK (${r.detail}, ${r.ms} ms)` : `Verifica fallita: ${r.detail}`, r.ok ? "info" : "error");
      reload();
    } catch (e) {
      toastError(e);
    } finally {
      setBusy("");
    }
  };
  const fetchModels = async () => {
    setBusy("fetch");
    try {
      await post(`/api/providers/${p.id}/models/fetch`);
      toast("Elenco dei modelli aggiornato");
      reload();
    } catch (e) {
      toastError(e);
    } finally {
      setBusy("");
    }
  };
  const shown = p.models.slice(0, 5);
  return (
    <article className={`card set-prov ${p.enabled ? "" : "is-off"}`}>
      <header className="set-prov-head">
        <span className="set-prov-ico">
          <Icon name={p.type === "openai_compat" || p.type === "fake" ? "cpu" : "globe"} />
        </span>
        <div className="grow" style={{ minWidth: 0 }}>
          <h3 className="set-prov-name">{p.name}</h3>
          <div className="set-prov-type">{p.type_label}</div>
          <div className="set-prov-badges">
            {p.enabled ? <span className="badge ok">Attivo</span> : <span className="badge warn">Disattivato</span>}
          </div>
        </div>
        <div className="set-prov-tools">
          <button className="btn sm ghost icon" onClick={onEdit} aria-label={`Modifica ${p.name}`} title="Modifica">
            <Icon name="pencil" />
          </button>
          <button className="btn sm ghost icon danger" onClick={onDelete} aria-label={`Elimina ${p.name}`} title="Elimina">
            <Icon name="trash" />
          </button>
        </div>
      </header>
      <dl className="set-prov-facts">
        <div>
          <dt>URL base</dt>
          <dd className="mono ellipsis" title={p.base_url || p.default_base_url || ""}>
            {p.base_url || p.default_base_url || "—"}
          </dd>
        </div>
        <div>
          <dt>Chiave API</dt>
          <dd>{p.has_api_key ? <span className="mono">{p.api_key_hint ?? "salvata"}</span> : <span className="faint">nessuna</span>}</dd>
        </div>
        <div className="wide">
          <dt>
            Modelli <span className="faint mono">{p.models.length}</span>
          </dt>
          <dd className="set-prov-models">
            {shown.length === 0 && <span className="faint">nessun modello</span>}
            {shown.map((m) => (
              <span key={m.id} className="badge mono" title={m.label ?? m.id}>
                {m.id}
              </span>
            ))}
            {p.models.length > shown.length && <span className="faint small">+{p.models.length - shown.length}</span>}
          </dd>
        </div>
      </dl>
      {p.unusable_reason && (
        <div className="alert warn small">
          <Icon name="alert-triangle" />
          <span>Non utilizzabile: {p.unusable_reason}</span>
        </div>
      )}
      {p.last_test && (
        <div className={`set-prov-test ${p.last_test.ok ? "" : "text-danger"}`}>
          <span className={`dot ${p.last_test.ok ? "ok" : "danger"}`} />
          <span>
            Ultima verifica {fmtDate(p.last_test.at, true)}: {p.last_test.ok ? "OK" : "fallita"} — {p.last_test.detail}
          </span>
        </div>
      )}
      <footer className="set-prov-actions">
        <button className="btn sm" onClick={test} disabled={!p.usable || busy !== ""}>
          <Icon name={busy === "test" ? "loader" : "activity"} className={busy === "test" ? "spin" : ""} />
          Verifica connessione
        </button>
        <button className="btn sm" onClick={fetchModels} disabled={!p.usable || busy !== ""}>
          <Icon name={busy === "fetch" ? "loader" : "refresh"} className={busy === "fetch" ? "spin" : ""} />
          Aggiorna modelli
        </button>
      </footer>
    </article>
  );
}

export default function ProvidersSettings() {
  const providers = useApi(() => get<ProviderRow[]>("/api/providers"));
  const types = useApi(() => get<ProviderType[]>("/api/providers/types"));
  const [editing, setEditing] = useState<ProviderRow | "new" | null>(null);
  const [deleting, setDeleting] = useState<ProviderRow | null>(null);

  if (providers.loading && !providers.data) return <Loading />;
  const list = providers.data ?? [];
  return (
    <div className="set-stack">
      <div className="alert accent set-intro">
        <Icon name="info" />
        <span>
          I provider leggono il materiale che carichi e rispondono in chat; quale usare per cosa si sceglie in <a href="#models">Modelli</a>. Il provider{" "}
          <strong>Fake</strong> restituisce risposte deterministiche per i test. Costi e chiamate sono in <a href="#ai">AI e costi</a>.
        </span>
      </div>
      <div className="section-label" style={{ margin: 0 }}>
        <span>
          Provider configurati <span className="mono">· {list.length}</span>
        </span>
        <button className="btn primary sm" onClick={() => setEditing("new")}>
          <Icon name="plus" />
          Aggiungi provider
        </button>
      </div>
      {list.length === 0 && <Empty icon="cpu">Nessun provider configurato.</Empty>}
      <div className="set-prov-grid">
        {list.map((p) => (
          <ProviderCard key={p.id} p={p} reload={providers.reload} onEdit={() => setEditing(p)} onDelete={() => setDeleting(p)} />
        ))}
      </div>
      {editing && types.data && (
        <ProviderForm initial={editing === "new" ? undefined : editing} types={types.data} onClose={() => setEditing(null)} onSaved={providers.reload} />
      )}
      {deleting && (
        <Confirm
          title="Elimina provider"
          danger
          message={`Eliminare ${deleting.name}? Le assegnazioni dei ruoli che lo usano vengono azzerate.`}
          confirmLabel="Elimina"
          onConfirm={async () => {
            await del(`/api/providers/${deleting.id}`);
            providers.reload();
          }}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
