import { useEffect, useState, type ReactNode } from "react";
import { get, put } from "../../lib/api";
import { toast, toastError } from "../ui";

/** Load/save one settings section. */
export function useSection<T extends Record<string, unknown>>(section: string) {
  const [value, setValue] = useState<T | null>(null);
  const [saving, setSaving] = useState(false);
  const load = () =>
    get<T>(`/api/settings/${section}`)
      .then(setValue)
      .catch(toastError);
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section]);
  const save = async (patch?: Partial<T>) => {
    setSaving(true);
    try {
      const saved = await put<T>(`/api/settings/${section}`, { ...(value ?? {}), ...(patch ?? {}) });
      setValue(saved);
      toast("Impostazioni salvate");
      return saved;
    } catch (e) {
      toastError(e);
      return null;
    } finally {
      setSaving(false);
    }
  };
  const set = <K extends keyof T>(k: K, v: T[K]) => setValue((cur) => (cur ? { ...cur, [k]: v } : cur));
  return { value, set, save, saving, reload: load };
}

export function NumberField({
  label,
  hint,
  value,
  onChange,
  min,
  max,
  step,
}: {
  label: string;
  hint?: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="field">
      {label} {hint && <span className="hint">{hint}</span>}
      <input type="number" value={value} min={min} max={max} step={step} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

/** A boolean setting: title + help text on the left, a switch on the right. The whole row is the label. */
export function ToggleRow({
  title,
  hint,
  checked,
  onChange,
  disabled,
}: {
  title: ReactNode;
  hint?: ReactNode;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="set-toggle">
      <span className="set-toggle-text">
        <span className="set-toggle-title">{title}</span>
        {hint && <span className="set-toggle-hint">{hint}</span>}
      </span>
      <span className="switch">
        <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        <span className="track" aria-hidden="true" />
      </span>
    </label>
  );
}

/** A settings card: uppercase label heading (with optional right-side content), body, optional footer actions. */
export function SetCard({ title, aside, children, actions, flush = false }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; actions?: ReactNode; flush?: boolean }) {
  return (
    <section className={`card set-card ${flush ? "flush" : ""}`}>
      {title && (
        <h3 className="section-label set-card-label">
          <span>{title}</span>
          {aside}
        </h3>
      )}
      <div className="set-card-body">{children}</div>
      {actions && <div className="set-actions">{actions}</div>}
    </section>
  );
}

/** Save button used at the bottom right of every card. */
export function SaveButton({ onClick, busy, label = "Salva", disabled }: { onClick: () => void; busy?: boolean; label?: string; disabled?: boolean }) {
  return (
    <button className="btn primary" type="button" disabled={busy || disabled} onClick={onClick}>
      {label}
    </button>
  );
}

// Italian names for the model roles and prompts (the backend's labels are the English fallback).
export const ROLE_IT: Record<string, string> = {
  vision: "Lettura delle pagine con la loro immagine (visione)",
  handwriting: "Trascrizione della scrittura a mano (visione)",
  writing: "Lettura del testo delle pagine e correzioni",
  classification: "Collocazione in materia e capitolo",
  chat: "Assistente AI (chat con strumenti)",
};

export const ROLE_SHORT_IT: Record<string, string> = {
  vision: "visione",
  handwriting: "scrittura a mano",
  writing: "testo",
  classification: "collocazione",
  chat: "assistente",
};

export const PROMPT_IT: Record<string, string> = {
  "read.pages": "Lettura delle pagine (testo, formule e immagini → LaTeX)",
  "read.handwriting": "Trascrizione della scrittura a mano (foto → LaTeX)",
  "notes.fix": "Correzione automatica degli errori di compilazione",
  "classify.place": "Collocazione (quale materia e capitolo)",
  "agent.system": "Assistente AI (chat con strumenti)",
  "agent.review": "Assistente AI: revisione del documento",
};
