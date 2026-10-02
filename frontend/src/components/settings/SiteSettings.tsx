import { SaveButton, SetCard, ToggleRow, useSection } from "./common";

interface Site {
  title: string;
  description: string;
  byline: string;
  allow_indexing: boolean;
  [k: string]: unknown;
}

export default function SiteSettings() {
  const s = useSection<Site>("site");
  if (!s.value) return null;
  const v = s.value;
  return (
    <div className="set-stack">
      <SetCard title="Sito pubblico" actions={<SaveButton onClick={() => s.save()} busy={s.saving} />}>
        <div className="stack">
          <label className="field">
            Titolo del sito
            <input type="text" value={v.title} onChange={(e) => s.set("title", e.target.value)} />
          </label>
          <label className="field">
            Firma <span className="hint">piccola riga sopra il titolo, es. «Mario Rossi · Università di Bologna»</span>
            <input type="text" value={v.byline ?? ""} maxLength={200} onChange={(e) => s.set("byline", e.target.value)} />
          </label>
          <label className="field">
            Descrizione
            <textarea rows={3} value={v.description} onChange={(e) => s.set("description", e.target.value)} />
          </label>
          <ToggleRow
            title="Indicizzazione nei motori di ricerca"
            hint="Consente l'indicizzazione delle pagine pubbliche (robots.txt e meta robots)."
            checked={v.allow_indexing}
            onChange={(x) => s.set("allow_indexing", x)}
          />
        </div>
      </SetCard>
    </div>
  );
}
