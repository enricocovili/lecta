// The text part of a lesson's undo history: an edit of the notes is kept as a small patch (what was cut and what went in),
// not as two copies of the whole text, and typing in bursts is merged into one step.

export interface NotesPatch {
  /** where the change starts */
  at: number;
  /** the text that was there before */
  del: string;
  /** the text that is there after */
  ins: string;
}

/** The patch that turns `a` into `b` (common start and end are left out). */
export function diffPatch(a: string, b: string): NotesPatch {
  const max = Math.min(a.length, b.length);
  let at = 0;
  while (at < max && a.charCodeAt(at) === b.charCodeAt(at)) at++;
  let end = 0;
  while (end < max - at && a.charCodeAt(a.length - 1 - end) === b.charCodeAt(b.length - 1 - end)) end++;
  // Do not cut a surrogate pair in half (emoji).
  if (at > 0 && at < b.length && b.charCodeAt(at - 1) >= 0xd800 && b.charCodeAt(at - 1) <= 0xdbff) at--;
  return { at, del: a.slice(at, a.length - end), ins: b.slice(at, b.length - end) };
}

/** Apply the patch to the text it was made for (`inverse`: to the text it produced, to get the old one back). */
export function applyPatch(text: string, p: NotesPatch, inverse = false): string {
  const [cut, put] = inverse ? [p.ins, p.del] : [p.del, p.ins];
  return text.slice(0, p.at) + put + text.slice(p.at + cut.length);
}

/** Where the caret belongs after applying (or undoing) the patch. */
export function caretAfter(p: NotesPatch, inverse = false): number {
  return p.at + (inverse ? p.del : p.ins).length;
}

/** Typing continues the same step while each edit is small and comes soon after the previous one. */
export const MERGE_MS = 1000;
export const MERGE_CHARS = 24;

export function mergeable(edit: NotesPatch, lastAt: number, now: number): boolean {
  return now - lastAt < MERGE_MS && edit.ins.length <= MERGE_CHARS && edit.del.length <= MERGE_CHARS;
}
