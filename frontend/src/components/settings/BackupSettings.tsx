import { useState } from "react";
import { Icon } from "../icons";
import { SetCard, ToggleRow } from "./common";

export default function BackupSettings() {
  const [withSources, setWithSources] = useState(false);
  return (
    <div className="set-stack">
      <SetCard
        title="Esportazione"
        actions={
          <a className="btn primary" href={`/api/export${withSources ? "?include_sources=true" : ""}`}>
            <Icon name="download" />
            Esporta tutto
          </a>
        }
      >
        <p className="set-help">
          Scarica uno zip con i sorgenti LaTeX di ogni materia (compreso il preambolo in uso), l'ultima bozza PDF e il PDF pubblicato. Viene trasmesso in
          streaming, quindi va bene anche per raccolte grandi.
        </p>
        <div className="set-toggles">
          <ToggleRow
            title="Includi i file originali caricati"
            hint="PDF, foto e Markdown così come li hai caricati."
            checked={withSources}
            onChange={setWithSources}
          />
        </div>
      </SetCard>
      <SetCard title="Backup completo e ripristino">
        <p className="set-help" style={{ color: "var(--fg)" }}>
          Per un backup completo (database, caricamenti, segreti, pubblicazioni) usa gli script del repository sul server:
        </p>
        <pre className="small">{`scripts/backup.sh              # → backups/lecta-YYYYmmdd-HHMM.tar.gz
scripts/restore.sh <archivio>  # ferma lo stack, ripristina DB e volumi, lo riavvia`}</pre>
        <p className="set-help">
          L'archivio contiene il dump di Postgres e i volumi <code>app-data</code> e <code>secrets</code> (lì c'è la chiave che cifra le tue chiavi API: conservalo
          al sicuro). Le cartelle di build non servono: vengono ricreate alla prossima compilazione. Vedi docs/DEPLOY.md → Backup and restore.
        </p>
      </SetCard>
    </div>
  );
}
