// The lab's assistant: the course's AI panel, on the lab. It knows the open file (and the lines picked with «Chiedi»), reads
// the lab, the theory lesson and its chapter, changes the lab's text files and comments them when asked; one click undoes a turn.
import { useMemo } from "react";
import AiPanel from "../workspace/AiPanel";
import type { QuickAction, ScopeView } from "../workspace/Composer";
import type { MessageCtx } from "../workspace/MessageView";
import type { ChangedFile, Scope, SelectionScope } from "../workspace/types";
import { useAssistant } from "../workspace/useAssistant";
import type { LabData, LabFile } from "./files";

const INTRO = {
  title: "Chiedi del laboratorio",
  text: "L’assistente legge i file del laboratorio, i tuoi commenti, gli appunti della lezione teorica e il suo capitolo. Seleziona delle righe e premi «Chiedi», oppure scrivi qui sotto. Non esegue mai il codice; le modifiche ai file sono immediate e si annullano con un clic, e commenta solo se glielo chiedi.",
};

const QUICK: QuickAction[] = [
  { label: "Spiega questo file", text: "Spiegami cosa fa questo file, passo per passo, collegandolo alla lezione teorica.", mode: "explain" },
  { label: "Trova errori", text: "Cerca errori o punti fragili nel codice di questo file e spiegameli, senza modificarlo.", mode: "explain" },
  { label: "Commenta il codice", text: "Commenta le parti importanti di questo file, con commenti brevi sulle righe giuste.", mode: "ask" },
  { label: "Collega alla teoria", text: "Collega il codice di questo file ai concetti della lezione teorica e del suo capitolo.", mode: "explain" },
];

export default function LabAssistant({
  lab,
  files,
  openFile,
  selection,
  open,
  focusNonce,
  onClose,
  onClearSelection,
  onChanged,
  onOpenFile,
}: {
  lab: LabData;
  files: LabFile[];
  openFile: LabFile | null;
  selection: SelectionScope | null;
  open: boolean;
  focusNonce: number;
  onClose: () => void;
  onClearSelection: () => void;
  /** a turn changed the lab (it is over, or it was undone) */
  onChanged: (files: ChangedFile[]) => void;
  onOpenFile: (path: string) => void;
}) {
  const assistant = useAssistant({ courseId: lab.lesson.course_id, labId: lab.id, onChange: (changed, final) => final && onChanged(changed) });
  const scope = useMemo<ScopeView | null>(
    () => (openFile ? { chapterId: null, chapterLabel: openFile.path, selection, pinned: !!selection } : null),
    [openFile, selection],
  );
  const ctx: MessageCtx = {
    chapters: [],
    onJump: () => undefined,
    onAsk: async (text, opts = {}) => {
      const s: Scope = opts.scope ?? { mode: opts.mode, file_id: openFile?.id ?? null, ...(selection && !opts.noScope ? { selection } : {}) };
      const ok = await assistant.send(text, s);
      if (ok && selection) onClearSelection();
      return ok;
    },
    onUndo: assistant.undo,
    onFile: onOpenFile,
    fileName: (id) => files.find((f) => f.id === id)?.path ?? null,
  };
  return (
    <div className={`lab-ai ${open ? "" : "off"}`} data-testid="lab-ai">
      <AiPanel
        assistant={assistant}
        ctx={ctx}
        scope={scope}
        open={open}
        focusNonce={focusNonce}
        prefill={null}
        onClearScope={onClearSelection}
        onClose={onClose}
        intro={INTRO}
        quick={QUICK}
      />
    </div>
  );
}
