import { useEffect, useState } from "react";
import { Icon } from "./icons";
import AccountSettings from "./settings/AccountSettings";
import AiSettings from "./settings/AiSettings";
import BackupSettings from "./settings/BackupSettings";
import { EmbeddingsSettings, UploadSettings } from "./settings/IndexSettings";
import LatexSettings from "./settings/LatexSettings";
import ModelsSettings from "./settings/ModelsSettings";
import PromptsSettings from "./settings/PromptsSettings";
import ProvidersSettings from "./settings/ProvidersSettings";
import SiteSettings from "./settings/SiteSettings";

// The keys are the URL hashes (/admin/settings#providers) — keep them stable.
const SECTIONS = [
  { key: "account", label: "Account", icon: "user", sub: "Password, verifica in due passaggi e sessioni attive." },
  { key: "site", label: "Sito", icon: "globe", sub: "Come appare il sito pubblico." },
  { key: "providers", label: "Provider", icon: "cpu", sub: "I servizi AI che leggono il materiale caricato e rispondono in chat." },
  { key: "models", label: "Modelli", icon: "layers", sub: "Quale provider e modello usare per ogni ruolo." },
  { key: "prompts", label: "Prompt", icon: "message", sub: "Le istruzioni di sistema, modificabili e con cronologia delle versioni." },
  { key: "ai", label: "AI e costi", icon: "activity", sub: "Limiti delle richieste, costi e registro delle chiamate." },
  { key: "latex", label: "LaTeX", icon: "code", sub: "Compilazione e preambolo condiviso." },
  { key: "embeddings", label: "Indice ed embeddings", icon: "database", sub: "Ricerca semantica locale e scelta del capitolo per il testo di una lezione." },
  { key: "uploads", label: "File", icon: "upload", sub: "Limiti delle slide caricate nelle lezioni." },
  { key: "backup", label: "Backup", icon: "archive", sub: "Esportazione e backup completo." },
] as const;
type Section = (typeof SECTIONS)[number]["key"];

export default function SettingsPage() {
  const [tab, setTab] = useState<Section>("account");
  useEffect(() => {
    const read = () => {
      const raw = location.hash.replace("#", "");
      const h = (raw === "privacy" ? "ai" : raw) as Section;
      if (SECTIONS.some((t) => t.key === h)) setTab(h);
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  // Phones: keep the active chip of the horizontal section row in view.
  useEffect(() => {
    if (!window.matchMedia("(max-width: 860px)").matches) return;
    document.querySelector(".set-nav > .active")?.scrollIntoView({ inline: "center", block: "nearest" });
  }, [tab]);
  const go = (t: Section) => {
    setTab(t);
    history.replaceState(null, "", `#${t}`);
  };
  const cur = SECTIONS.find((s) => s.key === tab)!;
  return (
    <div className="set-page">
      <div className="page-head">
        <h1>Impostazioni</h1>
      </div>
      <div className="set-layout">
        <nav className="set-nav" role="tablist" aria-label="Sezioni delle impostazioni" aria-orientation="vertical">
          {SECTIONS.map((s) => (
            <button key={s.key} type="button" role="tab" aria-selected={tab === s.key} className={tab === s.key ? "active" : ""} onClick={() => go(s.key)}>
              <Icon name={s.icon} />
              <span>{s.label}</span>
            </button>
          ))}
        </nav>
        <div className="set-body" role="tabpanel" aria-label={cur.label}>
          <header className="set-head">
            <h2 className="set-title">{cur.label}</h2>
            <p className="set-sub">{cur.sub}</p>
          </header>
          {tab === "account" && <AccountSettings />}
          {tab === "site" && <SiteSettings />}
          {tab === "providers" && <ProvidersSettings />}
          {tab === "models" && <ModelsSettings />}
          {tab === "prompts" && <PromptsSettings />}
          {tab === "ai" && <AiSettings />}
          {tab === "latex" && <LatexSettings />}
          {tab === "embeddings" && <EmbeddingsSettings />}
          {tab === "uploads" && <UploadSettings />}
          {tab === "backup" && <BackupSettings />}
        </div>
      </div>
    </div>
  );
}
