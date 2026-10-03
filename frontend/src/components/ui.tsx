import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../lib/api";
import { Icon } from "./icons";

// ------------------------------------------------------------------ toasts

type ToastMsg = { id: number; text: string; kind: "info" | "error" };
let toastId = 0;

export function toast(text: string, kind: "info" | "error" = "info") {
  window.dispatchEvent(new CustomEvent("lecta:toast", { detail: { id: ++toastId, text, kind } }));
}

export function toastError(e: unknown) {
  toast(e instanceof ApiError || e instanceof Error ? e.message : String(e), "error");
}

export function ToastHost() {
  const [items, setItems] = useState<ToastMsg[]>([]);
  useEffect(() => {
    const on = (e: Event) => {
      const t = (e as CustomEvent<ToastMsg>).detail;
      setItems((xs) => [...xs, t]);
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== t.id)), t.kind === "error" ? 7000 : 3500);
    };
    window.addEventListener("lecta:toast", on);
    return () => window.removeEventListener("lecta:toast", on);
  }, []);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`}>
          <Icon name={t.kind === "error" ? "alert-circle" : "check-circle"} />
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ data hooks

export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fnRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, reload, setData };
}

export function usePoll(fn: () => void, ms: number, active = true) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [ms, active]);
}

/** Wrap an async action: disables while running, reports errors as toasts. */
export function useAction<A extends unknown[]>(fn: (...args: A) => Promise<unknown>) {
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async (...args: A) => {
      setBusy(true);
      try {
        return await fn(...args);
      } catch (e) {
        toastError(e);
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [fn],
  );
  return [run, busy] as const;
}

// ------------------------------------------------------------------ components

export function Modal({
  title,
  children,
  onClose,
  wide = false,
  actions,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  actions?: ReactNode;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  // Esc closes; Enter does what the primary button does (not in a text area, where it is a new line, nor on a button or a
  // link, which have their own Enter; Ctrl+Enter confirms from a text area too). Only the dialog on top answers.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") return onClose();
      const el = dialog.current;
      if (e.key !== "Enter" || !el || e.isComposing || e.defaultPrevented || e.altKey || e.shiftKey) return;
      const dialogs = document.querySelectorAll(".modal-backdrop");
      if (dialogs[dialogs.length - 1] !== el.parentElement) return;
      const t = e.target as HTMLElement | null;
      if (t && t !== document.body && !el.contains(t)) return;
      const tag = t?.tagName;
      if (tag === "BUTTON" || tag === "A" || tag === "SELECT" || t?.isContentEditable) return;
      if (tag === "TEXTAREA" && !(e.ctrlKey || e.metaKey)) return;
      if (tag === "INPUT" && ["button", "submit", "file", "radio"].includes((t as HTMLInputElement).type)) return;
      const buttons = [...el.querySelectorAll<HTMLButtonElement>(".actions .btn.primary, .actions .btn.solid")].filter((b) => !b.disabled);
      const go = buttons[buttons.length - 1];
      if (!go) return;
      e.preventDefault();
      go.click();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  // A dialog that asks for something starts in its first one-line field, so it can be filled and confirmed from the keyboard.
  useEffect(() => {
    const el = dialog.current;
    if (!el || el.contains(document.activeElement)) return;
    const first = el.querySelector<HTMLInputElement>("input:not([type]), input[type=text], input[type=url], input[type=search], input[type=password], input[type=number]");
    if (first && !first.disabled) {
      first.focus({ preventScroll: true });
      first.select();
    } else {
      el.focus({ preventScroll: true }); // the button that opened the dialog must not answer Enter (it would open it again)
    }
  }, []);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={dialog} tabIndex={-1} className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="row between nowrap" style={{ alignItems: "flex-start" }}>
          <h2 className="modal-title">{title}</h2>
          <button className="btn ghost icon sm" onClick={onClose} aria-label="Chiudi">
            <Icon name="x" />
          </button>
        </div>
        <div style={{ marginTop: "1rem" }}>{children}</div>
        {actions && <div className="actions">{actions}</div>}
      </div>
    </div>
  );
}

export function Loading({ text = "Caricamento…" }: { text?: string }) {
  return (
    <div className="muted small row" style={{ padding: ".5rem 0" }}>
      <Icon name="loader" className="spin" />
      {text}
    </div>
  );
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div className="alert danger">
      <Icon name="alert-circle" />
      <span>{error}</span>
    </div>
  );
}

export function Progress({ value, tone = "" }: { value: number; tone?: "" | "ok" | "warn" | "danger" }) {
  return (
    <div className={`progress ${tone}`} aria-valuenow={Math.round(value * 100)} role="progressbar">
      <div style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

const STATUS_CLASS: Record<string, string> = {
  succeeded: "ok",
  ok: "ok",
  accepted: "ok",
  approved: "ok",
  published: "ok",
  assigned: "ok",
  running: "warn",
  testing: "warn",
  queued: "",
  pending: "warn",
  open: "warn",
  failed: "danger",
  error: "danger",
  rejected: "danger",
  cancelled: "",
  discarded: "",
  timeout: "danger",
  superseded: "",
};

export const STATUS_LABEL: Record<string, string> = {
  succeeded: "completato",
  ok: "ok",
  accepted: "accettata",
  approved: "approvata",
  published: "pubblicata",
  running: "in corso",
  testing: "in test",
  queued: "in coda",
  pending: "in attesa",
  failed: "fallito",
  error: "errore",
  rejected: "rifiutata",
  cancelled: "annullato",
  discarded: "scartato",
  timeout: "timeout",
  superseded: "superata",
  new: "nuovo",
  sent: "inviato",
  refused: "rifiutato",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${STATUS_CLASS[status] ?? ""}`}>{STATUS_LABEL[status] ?? status.replace(/_/g, " ")}</span>;
}

/** Overall state used across Home, sidebar and map: done / working / error. */
export type Tone = "ok" | "warn" | "danger" | "";

const TONE_ICON: Record<string, string> = { ok: "check", warn: "half", danger: "alert-circle", "": "clock" };

/** Status pill as in the mockups: icon + label on a soft background. */
export function StatusPill({ tone, label, size = "" }: { tone: Tone; label: string; size?: "" | "sm" }) {
  return (
    <span className={`pill ${tone} ${size}`}>
      <Icon name={TONE_ICON[tone]} />
      {label}
    </span>
  );
}

export function Dot({ tone, title }: { tone: Tone; title?: string }) {
  return <span className={`dot ${tone}`} title={title} aria-label={title} />;
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; disabled?: boolean }) {
  return (
    <label className="switch">
      {label !== undefined && <span>{label}</span>}
      <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="track" aria-hidden="true" />
    </label>
  );
}

export function Seg<T extends string>({ options, value, onChange, size = "" }: { options: { key: T; label: ReactNode }[]; value: T; onChange: (v: T) => void; size?: "" | "sm" }) {
  return (
    <div className={`seg ${size}`} role="tablist">
      {options.map((o) => (
        <button key={o.key} type="button" role="tab" aria-selected={value === o.key} className={value === o.key ? "active" : ""} onClick={() => onChange(o.key)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Empty({ icon = "inbox", children }: { icon?: string; children: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} />
      <div>{children}</div>
    </div>
  );
}

/** "ora", "07:31" (today), "ieri", "3 set" — the compact times of the activity list. */
export function fmtWhen(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  const now = new Date();
  const diff = (now.getTime() - d.getTime()) / 1000;
  if (diff < 90) return "ora";
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "ieri";
  return d.toLocaleDateString("it-IT", { day: "numeric", month: "short", ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { key: T; label: ReactNode }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.key} role="tab" aria-selected={value === t.key} className={value === t.key ? "active" : ""} onClick={() => onChange(t.key)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Confirm({
  title,
  message,
  confirmLabel = "Conferma",
  cancelLabel = "Annulla",
  danger = false,
  onConfirm,
  onCancel,
  onClose,
}: {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void | Promise<void>;
  /** Only the cancel button (not Esc or closing the dialog otherwise). */
  onCancel?: () => void;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={title}
      onClose={onClose}
      actions={
        <>
          <button
            className="btn"
            onClick={() => {
              onClose();
              onCancel?.();
            }}
          >
            {cancelLabel}
          </button>
          <button
            className={`btn ${danger ? "danger solid" : "primary"}`}
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
                onClose();
              } catch (e) {
                toastError(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            {confirmLabel}
          </button>
        </>
      }
    >
      <div>{message}</div>
    </Modal>
  );
}

export function useLocalStorage<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") return initial;
    try {
      const raw = window.localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (v: T) => {
      setValue(v);
      try {
        window.localStorage.setItem(key, JSON.stringify(v));
      } catch {
        /* ignore */
      }
    },
    [key],
  );
  return [value, set];
}
