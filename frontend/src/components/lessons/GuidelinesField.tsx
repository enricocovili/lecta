// The student's guidelines for writing a subject, with the notice for when there are none.
import { Icon } from "../icons";

export const GUIDELINES_MAX = 8000;

export const GUIDELINES_EXAMPLE =
  "Es. Scrivi in modo discorsivo, con un esempio per ogni definizione. Le dimostrazioni solo abbozzate. Metti in evidenza le formule da sapere a memoria.";

export default function GuidelinesField({ value, onChange, subject, rows = 6 }: { value: string; onChange: (v: string) => void; subject: string; rows?: number }) {
  const empty = value.trim() === "";
  return (
    <div className="stack tight">
      <label className="field">
        <span>
          Linee guida per «{subject}» <span className="hint">facoltative</span>
        </span>
        <textarea
          className="prose"
          rows={rows}
          value={value}
          maxLength={GUIDELINES_MAX}
          placeholder={GUIDELINES_EXAMPLE}
          onChange={(e) => onChange(e.target.value)}
          data-testid="guidelines"
        />
      </label>
      {empty ? (
        <div className="alert accent small" data-testid="guidelines-notice">
          <Icon name="info" />
          <span className="grow">
            Non hai inserito linee guida: <strong>genererò il testo in automatico</strong>, decidendo io struttura, tono e livello di dettaglio a partire dai
            tuoi appunti.
          </span>
        </div>
      ) : (
        <div className="small muted">Il testo seguirà queste indicazioni per struttura, tono e livello di dettaglio.</div>
      )}
    </div>
  );
}
