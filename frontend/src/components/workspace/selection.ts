// From blocks picked in the draft to what the assistant needs: the chapter, the source lines and their LaTeX.
// The draft is pictures typeset by LaTeX, so a selection is made of whole blocks (click, Shift+click to extend).
import type { DraftBlock, SelectionScope } from "./types";

export interface DraftSelection {
  chapterId: number;
  selection: SelectionScope;
  /** Viewport rectangle of the selection (for the floating toolbar). */
  rect: { top: number; bottom: number; left: number; right: number };
}

/** Blocks `from`…`to` (indices, inclusive) of a chapter, picked in the draft. */
export interface BlockPick {
  chapterId: number;
  from: number;
  to: number;
}

const MAX_TEXT = 6000;

/** A click on block `index`: picks it, extends the pick to it (Shift), or drops a pick of just that block. */
export function pickBlock(cur: BlockPick | null, chapterId: number, index: number, extend: boolean): BlockPick | null {
  if (extend && cur && cur.chapterId === chapterId) {
    const anchor = index < cur.from ? cur.to : cur.from;
    return { chapterId, from: Math.min(anchor, index), to: Math.max(anchor, index) };
  }
  if (cur && cur.chapterId === chapterId && cur.from === index && cur.to === index) return null;
  return { chapterId, from: index, to: index };
}

/** What the picked blocks are for the assistant: their lines and LaTeX source. */
export function pickScope(blocks: DraftBlock[], pick: BlockPick): SelectionScope | null {
  const picked = blocks.slice(pick.from, pick.to + 1);
  if (!picked.length) return null;
  const text = picked.map((b) => b.src ?? "").join("\n\n").trim();
  return { from_line: picked[0].start, to_line: picked[picked.length - 1].end, text: text.slice(0, MAX_TEXT) };
}

/** "selezione di 4 righe" */
export function selectionLabel(sel: SelectionScope): string {
  const n = sel.to_line ? sel.to_line - sel.from_line + 1 : sel.text.split("\n").filter((l) => l.trim()).length || 1;
  return `selezione di ${n} ${n === 1 ? "riga" : "righe"}`;
}
