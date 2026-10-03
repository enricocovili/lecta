// The AI panel: the conversation of the course (replies streamed live), what the assistant is doing,
// what it changed (with Annulla), the review card, and the composer.
import { useEffect, useRef } from "react";
import { fmtWhen } from "../ui";
import { Icon } from "../icons";
import Composer, { QUICK_ACTIONS, type ScopeView } from "./Composer";
import Pop from "./Pop";
import MessageView, { type MessageCtx } from "./MessageView";
import type { Assistant } from "./useAssistant";
import type { Mode } from "./types";

export default function AiPanel({
  assistant,
  ctx,
  scope,
  open,
  focusNonce,
  prefill,
  onClearScope,
  onClose,
}: {
  assistant: Assistant;
  ctx: MessageCtx;
  scope: ScopeView | null;
  /** Visible (the panel stays mounted while closed, so the draft message and the scroll survive). */
  open: boolean;
  focusNonce: number;
  prefill: { nonce: number; text: string } | null;
  onClearScope: () => void;
  /** Absent on phones, where the tab bar switches between the document and the assistant. */
  onClose?: () => void;
}) {
  const { messages, loading, busy, sessions, sessionId } = assistant;
  const list = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  // stay at the bottom while the reply grows, unless the user scrolled up to read
  useEffect(() => {
    const el = list.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);
  useEffect(() => {
    const el = list.current;
    if (open && el && stick.current) el.scrollTop = el.scrollHeight;
  }, [open]);

  const send = async (text: string, mode?: Mode) => {
    stick.current = true;
    return ctx.onAsk(text, { mode });
  };
  const empty = !loading && messages.length === 0;
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  // only the latest turn that changed something can be undone
  const lastChange = [...messages].reverse().find((m) => m.role === "assistant" && m.change && m.change.files.length + (m.change.chapters?.length ?? 0) > 0);

  return (
    <aside className="ai" data-testid="ai-panel" aria-label="Assistente AI">
      <div className="ai-head">
        <span className="ai-head-ico" aria-hidden="true">
          <Icon name="sparkles" />
        </span>
        <strong className="grow ellipsis">Assistente AI</strong>
        <Pop label="Conversazioni" className="ghost icon sm" menuClass="ai-history" summary={<Icon name="history" />}>
          <button type="button" onClick={() => void assistant.newSession()} disabled={busy || messages.length === 0}>
            <Icon name="plus" />
            Nuova conversazione
          </button>
          <button type="button" className="pg-danger" onClick={() => void assistant.deleteSession()} disabled={busy || sessionId === null}>
            <Icon name="trash" />
            Elimina questa conversazione
          </button>
          {sessions.length > 0 && <div className="sep" />}
          {sessions.slice(0, 12).map((s) => (
            <button key={s.id} type="button" className={s.id === sessionId ? "current" : ""} onClick={() => void assistant.switchSession(s.id)}>
              <span className="grow ellipsis">{s.title?.trim() || `Conversazione ${s.id}`}</span>
              <span className="tiny muted">{fmtWhen(s.updated_at ?? s.created_at)}</span>
            </button>
          ))}
        </Pop>
        {onClose && (
          <button type="button" className="btn ghost icon sm" onClick={onClose} aria-label="Chiudi l’assistente" title="Chiudi">
            <Icon name="x" />
          </button>
        )}
      </div>

      <div
        className="ai-list"
        ref={list}
        role="log"
        aria-live="polite"
        aria-label="Conversazione"
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        }}
      >
        {loading && (
          <div className="muted small ai-loading">
            <Icon name="loader" className="spin" />
            Carico la conversazione…
          </div>
        )}
        {empty && (
          <div className="ai-empty">
            <span className="ai-empty-ico">
              <Icon name="sparkles" />
            </span>
            <h2>Lavora sul documento</h2>
            <p>
              L’assistente conosce tutto il corso: capitoli, appunti e fonti. Seleziona un pezzo di testo nella bozza per chiedere una spiegazione o una modifica, oppure scrivi
              qui sotto. Le modifiche sono immediate e puoi annullarle con un clic.
            </p>
            <div className="ai-quick big">
              {QUICK_ACTIONS.map((q) => (
                <button key={q.label} type="button" className="ai-chip" onClick={() => send(q.text, q.mode)}>
                  {q.label}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <MessageView key={m.id} m={m} prev={i > 0 ? messages[i - 1] : null} last={m === lastAssistant} canUndo={m === lastChange} ctx={ctx} />
        ))}
      </div>

      <Composer
        busy={busy}
        scope={scope}
        focusNonce={focusNonce}
        prefill={prefill}
        showQuick={!empty && !busy}
        onSend={send}
        onStop={() => void assistant.cancel()}
        onClearScope={onClearScope}
      />
    </aside>
  );
}
