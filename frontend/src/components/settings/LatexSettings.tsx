import { useEffect, useState } from "react";
import { get, post, put } from "../../lib/api";
import { toast, toastError } from "../ui";
import { NumberField, SaveButton, SetCard, useSection } from "./common";

interface Latex {
  engine: string;
  timeout_s: number;
  autofix_iterations: number;
  [k: string]: unknown;
}

export default function LatexSettings() {
  const s = useSection<Latex>("latex");
  const [preamble, setPreamble] = useState<string>("");
  const [isDefault, setIsDefault] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get<{ preamble: string; is_default: boolean }>("/api/settings/template/effective")
      .then((t) => {
        setPreamble(t.preamble);
        setIsDefault(t.is_default);
      })
      .catch(toastError);
  }, []);

  if (!s.value) return null;
  const v = s.value;
  const tpl = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      toastError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="set-stack">
      <SetCard title="Compilazione" actions={<SaveButton onClick={() => s.save()} busy={s.saving} />}>
        <div className="form-grid">
          <label className="field">
            Motore predefinito <span className="hint">ogni materia può cambiarlo</span>
            <select value={v.engine} onChange={(e) => s.set("engine", e.target.value)}>
              <option value="pdflatex">pdflatex</option>
              <option value="xelatex">xelatex</option>
              <option value="lualatex">lualatex</option>
            </select>
          </label>
          <NumberField label="Timeout di compilazione (s)" value={v.timeout_s} min={10} max={1800} onChange={(x) => s.set("timeout_s", x)} />
          <NumberField
            label="Correzioni automatiche dopo un'importazione"
            hint="se il nuovo capitolo non compila; 0 = nessuna verifica"
            value={v.autofix_iterations}
            min={0}
            max={3}
            onChange={(x) => s.set("autofix_iterations", x)}
          />
        </div>
      </SetCard>
      <SetCard
        title="Template del preambolo globale"
        aside={isDefault ? <span className="badge">predefinito</span> : <span className="badge accent">personalizzato</span>}
        actions={
          <>
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                tpl(async () => {
                  const r = await post<{ preamble: string }>("/api/settings/template/reset");
                  setPreamble(r.preamble);
                  setIsDefault(true);
                  toast("Template ripristinato al predefinito");
                })
              }
            >
              Ripristina il predefinito
            </button>
            <button
              className="btn primary"
              disabled={busy}
              onClick={() =>
                tpl(async () => {
                  await put("/api/settings/template", { preamble });
                  setIsDefault(false);
                  toast("Template salvato");
                })
              }
            >
              Salva template
            </button>
          </>
        }
      >
        <p className="set-help">
          È condiviso dagli appunti e dalle compilazioni delle singole figure, quindi tieni fuori i pacchetti di impaginazione (geometry, hyperref): stanno nel{" "}
          <code>main.tex</code> di ogni materia. Deve definire <code>\review</code>, <code>\lectafigure</code> e <code>\lectaimage[didascalia]{"{images/…}"}</code> (le immagini copiate dalle sorgenti; se manca, l'app ne aggiunge una versione semplice). Ogni materia può sostituirlo nelle sue impostazioni.
        </p>
        <textarea className="set-code" rows={24} aria-label="Preambolo globale" value={preamble} onChange={(e) => setPreamble(e.target.value)} spellCheck={false} />
      </SetCard>
    </div>
  );
}
