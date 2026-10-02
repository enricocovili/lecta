import AiUsage from "../AiUsage";
import { Icon } from "../icons";
import { NumberField, SaveButton, SetCard, useSection } from "./common";

interface AI {
  max_output_tokens: number;
  chunk_pages: number;
  chunk_chars: number;
  request_timeout_s: number;
  max_concurrent_requests: number;
  agent_max_steps: number;
  [k: string]: unknown;
}

export default function AiSettings() {
  const ai = useSection<AI>("ai");
  return (
    <div className="set-stack">
      <div className="set-disclaimer small muted">
        <Icon name="info" />
        <span>
          Il materiale che carichi (testo, immagini delle pagine, foto) e i messaggi con l'assistente vengono inviati ai provider AI che configuri. Scegli provider di cui ti
          fidi e verifica le loro condizioni sulla conservazione dei dati.
        </span>
      </div>
      {ai.value && (
        <SetCard title="Richieste AI" actions={<SaveButton onClick={() => ai.save()} busy={ai.saving} />}>
          <div className="form-grid">
            <NumberField
              label="Pagine per richiesta di lettura"
              hint="un'importazione legge i file a blocchi di pagine, tutti in parallelo"
              value={ai.value.chunk_pages}
              min={1}
              onChange={(v) => ai.set("chunk_pages", v)}
            />
            <NumberField label="Caratteri massimi per richiesta" value={ai.value.chunk_chars} min={2000} onChange={(v) => ai.set("chunk_chars", v)} />
            <NumberField
              label="Token massimi in uscita per richiesta"
              hint="una risposta troncata viene divisa e riletta in due metà"
              value={ai.value.max_output_tokens}
              min={256}
              onChange={(v) => ai.set("max_output_tokens", v)}
            />
            <NumberField label="Timeout della richiesta (s)" value={ai.value.request_timeout_s} min={10} onChange={(v) => ai.set("request_timeout_s", v)} />
            <NumberField
              label="Richieste simultanee ai provider"
              hint="quanti blocchi vengono letti nello stesso momento"
              value={ai.value.max_concurrent_requests}
              min={1}
              max={16}
              onChange={(v) => ai.set("max_concurrent_requests", v)}
            />
            <NumberField
              label="Passi massimi dell'assistente per risposta"
              hint="quante azioni (letture, modifiche, compilazioni) può fare l'assistente in un turno; da 5 a 200"
              value={ai.value.agent_max_steps ?? 40}
              min={5}
              max={200}
              onChange={(v) => ai.set("agent_max_steps", v)}
            />
          </div>
        </SetCard>
      )}
      <SetCard title="Costi e chiamate">
        <AiUsage />
      </SetCard>
    </div>
  );
}
