import { useState } from "react";
import { fmtDate, get, post, put } from "../../lib/api";
import { Icon } from "../icons";
import { Loading, toast, toastError, useApi } from "../ui";
import { PROMPT_IT, ROLE_SHORT_IT } from "./common";

interface PromptRow {
  key: string;
  label: string;
  role: string;
  is_default: boolean;
  active_version: number | null;
  text: string;
  default_text: string;
  versions: { version: number; created_at: string; active: boolean; note: string | null }[];
}

function PromptEditor({ p, reload }: { p: PromptRow; reload: () => void }) {
  const [text, setText] = useState(p.text);
  const [note, setNote] = useState("");
  const dirty = text !== p.text;
  return (
    <div className="set-prompt-edit">
      <textarea rows={18} value={text} aria-label={`Testo del prompt ${PROMPT_IT[p.key] ?? p.label}`} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      <p className="set-help">
        <code>{"{nonce}"}</code> viene sostituito da un marcatore casuale per ogni richiesta, che delimita il contenuto caricato (non fidato): mantieni le regole di
        sicurezza.
      </p>
      <div className="set-prompt-bar">
        <input type="text" aria-label="Nota di versione" placeholder="Nota di versione (facoltativa)" value={note} onChange={(e) => setNote(e.target.value)} />
        <button
          className="btn"
          disabled={p.is_default}
          onClick={async () => {
            try {
              await post(`/api/prompts/${p.key}/reset`);
              setText(p.default_text);
              toast("Ripristinato il prompt predefinito");
              reload();
            } catch (e) {
              toastError(e);
            }
          }}
        >
          Ripristina il predefinito
        </button>
        <button
          className="btn primary"
          disabled={!dirty}
          onClick={async () => {
            try {
              const r = await put<{ version: number }>(`/api/prompts/${p.key}`, { text, note: note || null });
              toast(`Salvato come versione ${r.version}`);
              setNote("");
              reload();
            } catch (e) {
              toastError(e);
            }
          }}
        >
          Salva nuova versione
        </button>
      </div>
      {p.versions.length > 0 && (
        <div>
          <div className="section-label" style={{ margin: "1rem 0 .4rem" }}>
            Cronologia
          </div>
          <div className="rows set-versions">
            {p.versions.map((v) => (
              <div key={v.version} className="set-version">
                <span className="mono set-version-n">v{v.version}</span>
                <span className="grow small">
                  {fmtDate(v.created_at, true)} {v.note && <span className="muted">— {v.note}</span>}
                </span>
                {v.active ? (
                  <span className="badge ok">attiva</span>
                ) : (
                  <span className="row nowrap">
                    <button
                      className="btn xs"
                      onClick={async () => {
                        const r = await get<{ text: string }>(`/api/prompts/${p.key}/versions/${v.version}`);
                        setText(r.text);
                      }}
                    >
                      Carica
                    </button>
                    <button
                      className="btn xs"
                      onClick={async () => {
                        await post(`/api/prompts/${p.key}/versions/${v.version}/activate`);
                        reload();
                      }}
                    >
                      Attiva
                    </button>
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function PromptsSettings() {
  const { data, loading, reload } = useApi(() => get<PromptRow[]>("/api/prompts"));
  const [open, setOpen] = useState<string | null>(null);
  if (loading && !data) return <Loading />;
  return (
    <div className="set-stack">
      <p className="set-help" style={{ margin: 0 }}>
        Tutti i prompt di sistema sono modificabili e versionati. Finché non salvi una versione si usano quelli predefiniti.
      </p>
      <div className="card flush rows set-prompts">
        {(data ?? []).map((p) => {
          const isOpen = open === p.key;
          return (
            <div key={p.key} className={`set-prompt ${isOpen ? "open" : ""}`}>
              <button type="button" className="set-prompt-head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : p.key)}>
                <Icon name={isOpen ? "chevron-down" : "chevron-right"} className="set-prompt-caret" />
                <span className="grow set-prompt-title">
                  <strong>{PROMPT_IT[p.key] ?? p.label}</strong>
                  <span className="tiny faint mono">{p.key}</span>
                </span>
                <span className="set-prompt-badges">
                  <span className="badge">{ROLE_SHORT_IT[p.role] ?? p.role}</span>
                  {p.is_default ? <span className="badge">predefinito</span> : <span className="badge accent mono">v{p.active_version}</span>}
                </span>
              </button>
              {isOpen && <PromptEditor key={`${p.key}-${p.active_version}`} p={p} reload={reload} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}
