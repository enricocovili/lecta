// The lesson's assistant: the course's AI panel, asked about one page. With each question it gets the pictures of the pages
// around it (ten before and after, with the strokes) and the text and notes of the whole lesson; it only explains.
import { useMemo } from "react";
import AiPanel from "../workspace/AiPanel";
import type { QuickAction, ScopeView } from "../workspace/Composer";
import type { MessageCtx } from "../workspace/MessageView";
import type { Scope } from "../workspace/types";
import { useAssistant } from "../workspace/useAssistant";

const INTRO = {
  title: "Chiedi della slide",
  text: "L’assistente vede questa pagina e le dieci prima e dopo, con le tue scritte, e legge le slide e gli appunti di tutta la lezione. Chiedi il passaggio che non ti torna: risponde soltanto, senza cambiare nulla.",
};

const QUICK: QuickAction[] = [
  { label: "Spiega la slide", text: "Spiegami questa slide: cosa dice, perché serve e come si collega alle slide vicine.", mode: "explain" },
  { label: "Passo per passo", text: "Svolgi il procedimento di questa slide passo per passo, dicendo per ogni passo quale regola o proprietà usa e perché si può fare.", mode: "explain" },
  { label: "Un esempio numerico", text: "Fammi un esempio numerico semplice che mostri cosa succede in questa slide.", mode: "explain" },
  { label: "Cosa devo sapere prima?", text: "Cosa devo sapere per capire questa slide? Dimmi i prerequisiti e dove compaiono nella lezione.", mode: "explain" },
];

export interface AskedPage {
  id: number;
  label: string;
}

export default function LessonAssistant({
  lessonId,
  courseId,
  page,
  pinned,
  open,
  focusNonce,
  pageName,
  onClose,
  onUnpin,
}: {
  lessonId: number;
  courseId: number;
  /** the page the questions are about: the one picked with «Chiedi», or the one being looked at */
  page: AskedPage | null;
  /** picked with «Chiedi» (it stays while scrolling); otherwise the page follows the scroll */
  pinned: boolean;
  open: boolean;
  focusNonce: number;
  pageName: (id: number) => string | null;
  onClose: () => void;
  onUnpin: () => void;
}) {
  const assistant = useAssistant({ courseId, lessonId, onChange: () => undefined });
  const scope = useMemo<ScopeView | null>(() => (page ? { chapterId: null, chapterLabel: page.label, selection: null, pinned } : null), [page, pinned]);
  const ctx: MessageCtx = {
    chapters: [],
    onJump: () => undefined,
    onAsk: (text, opts = {}) => {
      const s: Scope = opts.scope ?? { mode: opts.mode ?? "explain", page_id: page?.id ?? null };
      return assistant.send(text, s);
    },
    onUndo: assistant.undo,
    pageName,
  };
  return (
    <div className={`les-ai ${open ? "" : "off"}`} data-testid="lesson-ai">
      <AiPanel assistant={assistant} ctx={ctx} scope={scope} open={open} focusNonce={focusNonce} prefill={null} onClearScope={onUnpin} onClose={onClose} intro={INTRO} quick={QUICK} />
    </div>
  );
}
