import { useEffect, useState } from "react";
import { fmtDate, get, post } from "../../lib/api";
import { Icon } from "../icons";
import { toast, toastError } from "../ui";
import { NumberField, SaveButton, SetCard, useSection } from "./common";

interface Emb {
  model: string;
  threads: number;
  min_chunks_per_s: number;
  benchmark: { model: string; threads: number; chunks_per_s: number; load_s: number; ok: boolean; error?: string; at?: string } | null;
  [k: string]: unknown;
}

interface Cat {
  threshold: number;
  top_k: number;
  [k: string]: unknown;
}

interface Uploads {
  max_file_mb: number;
  max_zip_uncompressed_mb: number;
  max_zip_members: number;
  max_compression_ratio: number;
  [k: string]: unknown;
}

interface IndexStatus {
  semantic: { enabled: boolean; reason: string };
  mode: string;
  chunks: number;
  embedded: number;
  chapters_indexed: number;
  chapters: number;
  available_models: string[];
}

const MODE_IT: Record<string, string> = { semantic: "semantica", lexical: "lessicale", hybrid: "ibrida" };

/** The backend explains why semantic retrieval is on/off in English: translate the known reasons. */
function reasonIt(r: string): string {
  const rules: [RegExp, string][] = [
    [/^ok$/, "tutto a posto"],
    [/^embeddings are turned off$/, "gli embeddings sono spenti"],
    [/^model (.+) is not available in this image$/, "il modello $1 non è disponibile in questa immagine"],
    [/^not benchmarked yet$/, "benchmark non ancora eseguito"],
    [/^benchmark failed: (.*)$/, "benchmark fallito: $1"],
    [/^the benchmark was run for another model$/, "il benchmark è stato eseguito per un altro modello"],
    [/^too slow \((.+) chunks\/s < (.+)\)$/, "troppo lento ($1 frammenti/s < $2)"],
  ];
  for (const [re, it] of rules) if (re.test(r)) return r.replace(re, it);
  return r;
}

export function EmbeddingsSettings() {
  const s = useSection<Emb>("embeddings");
  const c = useSection<Cat>("categorization");
  const [st, setSt] = useState<IndexStatus | null>(null);
  const load = () => get<IndexStatus>("/api/index/status").then(setSt).catch(() => undefined);
  useEffect(() => {
    load();
  }, []);
  if (!s.value) return null;
  const b = s.value.benchmark;
  const startJob = async (path: string, msg: string) => {
    try {
      const r = await post<{ job_id: number }>(path);
      toast(`${msg} (job #${r.job_id})`);
    } catch (e) {
      toastError(e);
    }
  };
  return (
    <div className="set-stack">
      {st && (
        <div className="set-stats">
          <div className="card stat">
            <span className="label">
              <span className={`dot ${st.semantic.enabled ? "ok" : "warn"}`} />
              Modalità
            </span>
            <span className="value">{MODE_IT[st.mode] ?? st.mode}</span>
          </div>
          <div className="card stat">
            <span className="label">Capitoli indicizzati</span>
            <span className="value">
              {st.chapters_indexed}
              <span className="set-stat-of">/{st.chapters}</span>
            </span>
          </div>
          <div className="card stat">
            <span className="label">Frammenti</span>
            <span className="value">{st.chunks.toLocaleString("it-IT")}</span>
          </div>
          <div className="card stat">
            <span className="label">Con embedding</span>
            <span className="value">{st.embedded.toLocaleString("it-IT")}</span>
          </div>
        </div>
      )}
      <SetCard
        title="Embeddings locali (ricerca semantica)"
        actions={
          <>
            <button className="btn" onClick={() => startJob("/api/settings/embeddings/benchmark", "Benchmark avviato")}>
              <Icon name="activity" />
              Avvia benchmark
            </button>
            <button className="btn" onClick={() => startJob("/api/index/rebuild", "Ricostruzione dell'indice avviata")}>
              <Icon name="refresh" />
              Ricostruisci indice
            </button>
            <SaveButton
              busy={s.saving}
              onClick={async () => {
                await s.save();
                load();
              }}
            />
          </>
        }
      >
        <p className="set-help">
          Un piccolo modello multilingue gira in locale sulla CPU (ONNX quantizzato, 384 dimensioni) per capire dove va il materiale nuovo. Se è spento,
          non disponibile o più lento della soglia, la collocazione usa la ricerca lessicale (full-text).
        </p>
        {st && (
          <div className={`alert small ${st.semantic.enabled ? "ok" : "warn"}`}>
            <Icon name={st.semantic.enabled ? "check-circle" : "info"} />
            <span>
              Modalità di ricerca: <strong>{MODE_IT[st.mode] ?? st.mode}</strong> — {reasonIt(st.semantic.reason)}. Indice: {st.chapters_indexed}/{st.chapters} capitoli,{" "}
              {st.chunks} frammenti ({st.embedded} con embedding).
            </span>
          </div>
        )}
        <div className="form-grid">
          <label className="field">
            Modello
            <select value={s.value.model} onChange={(e) => s.set("model", e.target.value)}>
              {(st?.available_models ?? [s.value.model]).map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
              <option value="off">spento (solo lessicale)</option>
            </select>
          </label>
          <NumberField label="Thread ONNX" hint="la CPU ha 4 core" value={s.value.threads} min={1} max={4} onChange={(v) => s.set("threads", v)} />
          <NumberField
            label="Soglia di fallback (frammenti/s)"
            hint="sotto questa velocità si usa la ricerca lessicale"
            value={s.value.min_chunks_per_s}
            min={0}
            step={0.5}
            onChange={(v) => s.set("min_chunks_per_s", v)}
          />
        </div>
        <div className="set-bench">
          <span className="set-bench-k">Benchmark</span>
          {b ? (
            b.ok ? (
              <span>
                <strong className="mono">{b.chunks_per_s} frammenti/s</strong> con {b.threads} thread, modello caricato in {b.load_s} s{" "}
                {b.at && <span className="muted">({fmtDate(b.at, true)})</span>}
              </span>
            ) : (
              <span className="text-danger">fallito: {b.error}</span>
            )
          ) : (
            <span className="muted">non ancora eseguito (parte da solo all'avvio del worker)</span>
          )}
        </div>
      </SetCard>
      {c.value && (
        <SetCard title="Smistamento" actions={<SaveButton onClick={() => c.save()} busy={c.saving} />}>
          <div className="form-grid">
            <NumberField
              label="Soglia di confidenza"
              hint="sotto questa soglia il materiale va in Da smistare"
              value={c.value.threshold}
              min={0}
              max={1}
              step={0.05}
              onChange={(v) => c.set("threshold", v)}
            />
            <NumberField label="Capitoli candidati (top-k)" value={c.value.top_k} min={1} max={50} onChange={(v) => c.set("top_k", v)} />
          </div>
        </SetCard>
      )}
    </div>
  );
}

export function UploadSettings() {
  const s = useSection<Uploads>("uploads");
  if (!s.value) return null;
  return (
    <SetCard title="Limiti dei file" actions={<SaveButton onClick={() => s.save()} busy={s.saving} />}>
      <div className="form-grid">
        <NumberField label="Dimensione massima delle slide di una lezione (MB)" value={s.value.max_file_mb} min={1} onChange={(v) => s.set("max_file_mb", v)} />
      </div>
    </SetCard>
  );
}
