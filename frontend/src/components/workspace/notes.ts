// Draft notes (decision 128): comments and suggestions that readers leave on a block of someone else's published course.
// A note is anchored to the block as its author saw it; the server places an open one again in the chapter as it is
// now, and flags it `changed` («testo cambiato») when that block's text is gone.
import type { DraftBlock } from "./types";

export type NoteKind = "comment" | "suggestion";
export type NoteStatus = "open" | "accepted" | "rejected" | "resolved";

export interface DraftNote {
  id: number;
  course_id: number;
  chapter_id: number;
  kind: NoteKind;
  status: NoteStatus;
  /** The block's LaTeX when the note was written. */
  src: string;
  start: number;
  end: number;
  /** Open, and the block's text is no longer in the chapter. */
  changed: boolean;
  /** The comment, or why the suggestion. */
  body: string;
  /** A suggestion's new LaTeX for the block. */
  proposed: string | null;
  author_id: number;
  author: string | null;
  mine: boolean;
  resolved_by: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export const NOTE_KIND: Record<NoteKind, string> = { comment: "Commento", suggestion: "Suggerimento" };
export const NOTE_STATUS: Record<NoteStatus, string> = { open: "Aperta", accepted: "Accettata", rejected: "Rifiutata", resolved: "Risolta" };

/** Is `b` the block the note is about: the same first line and the same text (the draft's `src` may be cut short). */
function holds(b: DraftBlock, n: DraftNote): boolean {
  const src = b.src ?? "";
  return b.start === n.start && !!src && n.src.slice(0, src.length) === src;
}

/** A chapter's notes on its blocks (by index), and apart those whose block isn't there any more (its text changed). */
export function placeNotes(blocks: DraftBlock[], notes: DraftNote[]): { onBlock: Map<number, DraftNote[]>; apart: DraftNote[] } {
  const onBlock = new Map<number, DraftNote[]>();
  const apart: DraftNote[] = [];
  for (const n of notes) {
    const k = n.changed ? -1 : blocks.findIndex((b) => holds(b, n));
    if (k < 0) apart.push(n);
    else onBlock.set(k, [...(onBlock.get(k) ?? []), n]);
  }
  return { onBlock, apart };
}

/** What a note sends: a comment needs its text; a suggestion its LaTeX (that differs from the block's), the reason optional. */
export function noteProblem(kind: NoteKind, body: string, proposed: string, src?: string): string | null {
  if (kind === "comment") return body.trim() ? null : "Scrivi il commento";
  return src !== undefined && proposed === src ? "Cambia il testo del blocco per suggerire una modifica" : null;
}
