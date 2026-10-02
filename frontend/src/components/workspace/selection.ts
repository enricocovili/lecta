// From a text selection in the draft to what the assistant needs: the chapter, the source lines and the text.
import type { SelectionScope } from "./types";

export interface DraftSelection {
  chapterId: number;
  selection: SelectionScope;
  /** Viewport rectangle of the selection (for the floating toolbar). */
  rect: { top: number; bottom: number; left: number; right: number };
}

const MAX_TEXT = 6000;

/** The selected text; each rendered formula becomes `$tex$` (from its data-tex). */
function selectedText(range: Range): string {
  const holder = document.createElement("div");
  holder.appendChild(range.cloneContents());
  holder.querySelectorAll<HTMLElement>(".math").forEach((el) => {
    el.replaceWith(document.createTextNode(`$${el.dataset.tex ?? el.textContent ?? ""}$`));
  });
  holder.querySelectorAll(".katex-mathml").forEach((el) => el.remove());
  // innerText follows block boundaries, but only for elements in the document.
  holder.style.cssText = "position:fixed;left:-99999px;top:0;width:640px;white-space:normal;opacity:0;pointer-events:none";
  document.body.appendChild(holder);
  const text = holder.innerText;
  holder.remove();
  return text.replace(/ /g, " ").replace(/\n{3,}/g, "\n\n").trim();
}

/** Grow the range so it never cuts a formula in two. */
function wholeFormulas(range: Range): Range {
  const r = range.cloneRange();
  const up = (n: Node) => (n.nodeType === Node.ELEMENT_NODE ? (n as Element) : n.parentElement)?.closest(".math") ?? null;
  const a = up(r.startContainer);
  const b = up(r.endContainer);
  if (a) r.setStartBefore(a);
  if (b) r.setEndAfter(b);
  return r;
}

/** `root` holds one `[data-chapter-id]` section per chapter; blocks carry `data-line`. */
export function readSelection(root: HTMLElement, sel: globalThis.Selection | null): DraftSelection | null {
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const original = sel.getRangeAt(0);
  if (!root.contains(original.commonAncestorContainer)) return null;
  const range = wholeFormulas(original);

  const startEl = (range.startContainer.nodeType === Node.ELEMENT_NODE ? (range.startContainer as Element) : range.startContainer.parentElement) as HTMLElement | null;
  const section = startEl?.closest<HTMLElement>("[data-chapter-id]");
  if (!section || !root.contains(section)) return null;
  const chapterId = Number(section.dataset.chapterId);

  const text = selectedText(range);
  if (!text) return null;

  const blocks = Array.from(section.querySelectorAll<HTMLElement>("[data-line]"));
  let from = Infinity;
  let last: HTMLElement | null = null;
  let lastLine = -1;
  for (const b of blocks) {
    if (!range.intersectsNode(b)) continue;
    const line = Number(b.dataset.line);
    if (!Number.isFinite(line)) continue;
    from = Math.min(from, line);
    if (line >= lastLine) {
      lastLine = line;
      last = b;
    }
  }
  if (!Number.isFinite(from)) return null;
  // The selection ends where the next block after the last selected one begins.
  let to: number | undefined;
  if (last) {
    for (const b of blocks) {
      if (b === last || last.contains(b) || !(last.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
      const line = Number(b.dataset.line);
      if (Number.isFinite(line) && line > from) to = Math.max(from, line - 1);
      break;
    }
  }

  const rects = range.getClientRects();
  const rr = rects.length ? rects : [range.getBoundingClientRect()];
  let top = Infinity;
  let bottom = -Infinity;
  let left = Infinity;
  let right = -Infinity;
  for (const r of Array.from(rr)) {
    if (r.width === 0 && r.height === 0) continue;
    top = Math.min(top, r.top);
    bottom = Math.max(bottom, r.bottom);
    left = Math.min(left, r.left);
    right = Math.max(right, r.right);
  }
  if (!Number.isFinite(top)) return null;

  return {
    chapterId,
    selection: { from_line: from, ...(to !== undefined ? { to_line: to } : {}), text: text.slice(0, MAX_TEXT) },
    rect: { top, bottom, left, right },
  };
}

/** "selezione di 4 righe" */
export function selectionLabel(sel: SelectionScope): string {
  const n = sel.to_line ? sel.to_line - sel.from_line + 1 : sel.text.split("\n").filter((l) => l.trim()).length || 1;
  return `selezione di ${n} ${n === 1 ? "riga" : "righe"}`;
}
