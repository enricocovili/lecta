// The lesson's assistant, asked in place: a small popup under the slide's corner buttons. One question about the page (or the
// area picked on it), one answer: the assistant sees the pages around it with the strokes and reads the whole lesson, and only
// explains. The answer goes into the page's notes with a click, as a step of the lesson's history; a new question replaces it.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import MessageView, { type MessageCtx } from "../workspace/MessageView";
import type { Scope } from "../workspace/types";
import { useAssistant } from "../workspace/useAssistant";
import type { Rect } from "./ink";

const QUICK = [
  { label: "Spiega la slide", text: "Spiegami questa slide: cosa dice, perché serve e come si collega alle slide vicine." },
  { label: "Passo per passo", text: "Svolgi il procedimento di questa slide passo per passo, dicendo per ogni passo quale regola o proprietà usa e perché si può fare." },
  { label: "Un esempio numerico", text: "Fammi un esempio numerico semplice che mostri cosa succede in questa slide." },
  { label: "Cosa devo sapere prima?", text: "Cosa devo sapere per capire questa slide? Dimmi i prerequisiti e dove compaiono nella lezione." },
];

export default function SlideAsk({
  lessonId,
  courseId,
  pageId,
  label,
  area,
  onKeep,
  onDropArea,
  onClose,
}: {
  lessonId: number;
  courseId: number;
  pageId: number;
  label: string;
  /** the area picked on the page with the select tool, if any: the question is about it */
  area: Rect | null;
  /** put an answer at the end of the page's notes; false when it could not */
  onKeep: (text: string) => boolean;
  onDropArea: () => void;
  onClose: () => void;
}) {
  const assistant = useAssistant({ courseId, lessonId, onChange: () => undefined, history: false });
  const [text, setText] = useState("");
  const [replyId, setReplyId] = useState<number | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const i = replyId === null ? -1 : assistant.messages.findIndex((m) => m.id === replyId);
  const reply = i >= 0 ? assistant.messages[i] : null;
  const question = i > 0 ? assistant.messages[i - 1] : null;

  useEffect(() => field.current?.focus({ preventScroll: true }), []);
  // grow with the text, up to a limit
  useLayoutEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    if (el.scrollHeight) el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [text]);
  // An answer nobody will see is not finished: closing the popup stops it.
  const live = useRef<() => void>(() => undefined);
  live.current = () => {
    if (reply?.status === "streaming") void assistant.cancel();
  };
  useEffect(() => () => live.current(), []);

  const ask = async (q: string, scope?: Scope) => {
    const s: Scope = scope ?? { mode: "explain", page_id: pageId, ...(area ? { region: [area.x0, area.y0, area.x1, area.y1] } : {}) };
    const id = await assistant.ask(q, s);
    if (id === null) return false;
    setReplyId(id);
    return true;
  };
  const submit = async () => {
    const q = text.trim();
    if (!q || assistant.busy) return;
    setText("");
    if (!(await ask(q))) setText(q);
  };
  const ctx: MessageCtx = {
    chapters: [],
    onJump: () => undefined,
    onAsk: (t, opts = {}) => ask(t, opts.scope),
    onUndo: assistant.undo,
    onKeep: (m) => onKeep(m.content),
  };

  return (
    <div
      className="les-ask"
      role="dialog"
      aria-label={`Chiedi all’assistente di ${label}`}
      data-testid="slide-ask"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="les-ask-head">
        <Icon name="sparkles" />
        <strong className="grow ellipsis">{label}</strong>
        {area && (
          <span className="les-ask-area" data-testid="slide-ask-area">
            area scelta
            <button type="button" onClick={onDropArea} aria-label="Chiedi di tutta la pagina" title="Chiedi di tutta la pagina">
              <Icon name="x" />
            </button>
          </span>
        )}
        <button type="button" className="btn ghost icon xs" onClick={onClose} aria-label="Chiudi" title="Chiudi (Esc)">
          <Icon name="x" />
        </button>
      </div>
      {reply ? (
        <div className="les-ask-answer" role="log" aria-live="polite">
          {question && <p className="les-ask-q">{question.content}</p>}
          <MessageView m={reply} prev={question} last={false} canUndo={false} ctx={ctx} />
        </div>
      ) : (
        <div className="ai-quick les-ask-quick" aria-label="Azioni rapide">
          {QUICK.map((q) => (
            <button key={q.label} type="button" className="ai-chip" disabled={assistant.loading} onClick={() => void ask(q.text)}>
              {q.label}
            </button>
          ))}
        </div>
      )}
      <div className="ai-input les-ask-input">
        <textarea
          ref={field}
          data-testid="slide-ask-input"
          aria-label="La tua domanda"
          rows={1}
          value={text}
          placeholder={assistant.busy ? "L’assistente sta rispondendo…" : reply ? "Un’altra domanda su questa slide" : "Cosa non ti torna di questa slide?"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void submit();
            }
          }}
        />
        {assistant.busy ? (
          <button type="button" className="btn danger sm ai-stop" onClick={() => void assistant.cancel()} aria-label="Ferma l’assistente" data-testid="ai-stop">
            <Icon name="x" />
            Stop
          </button>
        ) : (
          <button type="button" className="btn primary icon ai-send" onClick={() => void submit()} disabled={!text.trim() || assistant.loading} aria-label="Invia" data-testid="slide-ask-send">
            <Icon name="send" />
          </button>
        )}
      </div>
    </div>
  );
}
