// The message box of the assistant: multi-line, Enter to send, the scope pill and the quick actions.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { selectionLabel } from "./selection";
import type { Mode, SelectionScope } from "./types";

export interface ScopeView {
  chapterId: number | null;
  chapterLabel: string | null;
  chapterTitle?: string;
  selection: SelectionScope | null;
  /** The chat was opened from the selection menu: what the user is expected to write. */
  intent?: "explain" | "fix" | null;
  /** Removing it: the selection was picked by hand (true) or it is just the chapter being read (false). */
  pinned: boolean;
}

export type QuickAction = { label: string; text: string; mode: Mode };

export const QUICK_ACTIONS: QuickAction[] = [
  { label: "Riassumi il capitolo", text: "Riassumi il capitolo in pochi punti chiari, per un ripasso veloce.", mode: "explain" },
  { label: "Trova lacune rispetto alle fonti", text: "Confronta il testo con le fonti caricate e dimmi cosa manca o è trattato in modo incompleto.", mode: "explain" },
  { label: "Migliora la struttura", text: "Migliora la struttura: ordine degli argomenti, titoli delle sezioni e passaggi tra un punto e l'altro.", mode: "edit" },
  { label: "Aggiungi esempi", text: "Aggiungi esempi svolti nei punti in cui aiutano a capire.", mode: "edit" },
];

const PLACEHOLDER: Record<string, string> = {
  "": "Chiedi qualcosa o descrivi una modifica…",
  explain: "Cosa non ti è chiaro di questo passaggio?",
  fix: "Cosa c’è da correggere in questo passaggio?",
};

export default function Composer({
  busy,
  scope,
  focusNonce,
  prefill,
  showQuick,
  quick = QUICK_ACTIONS,
  onSend,
  onStop,
  onClearScope,
}: {
  busy: boolean;
  scope: ScopeView | null;
  focusNonce: number;
  prefill: { nonce: number; text: string } | null;
  showQuick: boolean;
  quick?: QuickAction[];
  onSend: (text: string, mode?: Mode) => Promise<boolean> | boolean;
  onStop: () => void;
  onClearScope: () => void;
}) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (focusNonce > 0) area.current?.focus();
  }, [focusNonce]);
  useEffect(() => {
    if (!prefill) return;
    setText(prefill.text);
    setTimeout(() => {
      const el = area.current;
      if (el) {
        el.focus();
        el.setSelectionRange(el.value.length, el.value.length);
      }
    }, 0);
  }, [prefill]);

  // grow with the text, up to a limit
  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    if (el.scrollHeight) el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [text]);

  const submit = async () => {
    const t = text.trim();
    if (!t || busy) return;
    setText("");
    if (!(await onSend(t))) setText(t);
  };

  return (
    <div className="ai-composer" data-testid="ai-composer-box">
      {showQuick && !busy && text === "" && (
        <div className="ai-quick" aria-label="Azioni rapide">
          {quick.map((q) => (
            <button key={q.label} type="button" className="ai-chip" onClick={() => onSend(q.text, q.mode)}>
              {q.label}
            </button>
          ))}
        </div>
      )}
      {scope && (scope.chapterLabel || scope.selection) && (
        <div className="ai-scope-row">
          <span className="ai-scope-pill" title={scope.chapterTitle}>
            <Icon name={scope.selection ? "pencil" : "file-text"} />
            <span className="ai-scope-text">
              {scope.chapterLabel}
              {scope.selection && <> · {selectionLabel(scope.selection)}</>}
            </span>
            <button type="button" className="ai-scope-x" onClick={onClearScope} aria-label="Togli il contesto">
              <Icon name="x" />
            </button>
          </span>
        </div>
      )}
      <div className="ai-input">
        <textarea
          ref={area}
          data-testid="ai-composer"
          aria-label="Scrivi all’assistente"
          rows={1}
          value={text}
          placeholder={busy ? "L’assistente sta lavorando…" : PLACEHOLDER[scope?.selection ? (scope.intent ?? "") : ""] ?? PLACEHOLDER[""]}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void submit();
            }
          }}
        />
        {busy ? (
          <button type="button" className="btn danger sm ai-stop" onClick={onStop} aria-label="Ferma l’assistente" data-testid="ai-stop">
            <Icon name="x" />
            Stop
          </button>
        ) : (
          <button type="button" className="btn primary icon ai-send" onClick={() => void submit()} disabled={!text.trim()} aria-label="Invia" data-testid="ai-send">
            <Icon name="send" />
          </button>
        )}
      </div>
      <div className="ai-hint tiny faint">
        <kbd>Invio</kbd> per inviare · <kbd>Maiusc</kbd>+<kbd>Invio</kbd> per andare a capo
      </div>
    </div>
  );
}
