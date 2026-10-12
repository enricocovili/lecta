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

export type DiffPart = { op: "same" | "del" | "add"; text: string };

/** What a suggestion changes in its block, word by word (spaces kept): the parts left, taken out and put in. */
export function wordDiff(from: string, to: string, limit = 1_000_000): DiffPart[] {
  const a = from.split(/(\s+)/).filter(Boolean);
  const b = to.split(/(\s+)/).filter(Boolean);
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const x = a.slice(head, a.length - tail);
  const y = b.slice(head, b.length - tail);
  const mid: DiffPart[] = [];
  if (x.length * y.length > limit) {
    // too long to compare word by word: the whole middle went out and came in
    mid.push({ op: "del", text: x.join("") }, { op: "add", text: y.join("") });
  } else {
    // longest common subsequence, from the end, then walked from the start
    const w = y.length + 1;
    const L = new Uint32Array((x.length + 1) * w);
    for (let i = x.length - 1; i >= 0; i--)
      for (let j = y.length - 1; j >= 0; j--) L[i * w + j] = x[i] === y[j] ? L[(i + 1) * w + j + 1] + 1 : Math.max(L[(i + 1) * w + j], L[i * w + j + 1]);
    let i = 0;
    let j = 0;
    while (i < x.length || j < y.length) {
      if (i < x.length && j < y.length && x[i] === y[j]) mid.push({ op: "same", text: x[i++] }), j++;
      else if (i < x.length && (j === y.length || L[(i + 1) * w + j] >= L[i * w + j + 1])) mid.push({ op: "del", text: x[i++] });
      else mid.push({ op: "add", text: y[j++] });
    }
  }
  const parts = [{ op: "same" as const, text: a.slice(0, head).join("") }, ...mid, { op: "same" as const, text: a.slice(a.length - tail).join("") }];
  const out: DiffPart[] = [];
  for (const p of parts) {
    if (!p.text) continue;
    const last = out[out.length - 1];
    if (last && last.op === p.op) last.text += p.text;
    else out.push({ ...p });
  }
  return out;
}
