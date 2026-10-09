// The lesson's own search (Ctrl+F, instead of the browser's, which cannot read the slides: they are pictures): the bar under
// the toolbar, the occurrences marked on the slides, on the typed text and in the notes, and the one being looked at brought
// into view.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Icon } from "../icons";
import type { Rect } from "./ink";
import { findAll, firstFrom, foldQuery, searchLesson, type Hit, type Range, type SlideText } from "./search";
import { slideTextOf, type PdfDoc } from "./SlideView";
import type { PageState } from "./useLesson";

/** What a page shows of the search: its occurrences and, when it has it, the one being looked at. */
export interface PageFind {
  query: string;
  hits: Hit[];
  current: Hit | null;
  /** changes each time the bar moves to an occurrence (also to the same one again) */
  seek: number;
}

/** The search of the editor: the bar's state, the occurrences, and what each page shows of them. */
export function useLessonFind(pages: PageState[], doc: PdfDoc | null, current: number) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(-1);
  const [seek, setSeek] = useState(0);
  const [slides, setSlides] = useState<Map<number, SlideText> | null>(null);
  const [focusNonce, setFocusNonce] = useState(0);

  // The text of the slides is read the first time the bar opens.
  const slideNos = useMemo(() => pages.flatMap((p) => (p.kind === "slide" && p.slide_page ? [p.slide_page] : [])).join(","), [pages]);
  useEffect(() => {
    if (!open || !doc) return;
    let cancelled = false;
    const nos = slideNos ? slideNos.split(",").map(Number) : [];
    Promise.all(nos.map((n) => slideTextOf(doc, n).then((t) => [n, t] as const, () => null))).then((all) => {
      if (!cancelled) setSlides(new Map(all.filter((x) => x !== null)));
    });
    return () => {
      cancelled = true;
    };
  }, [open, doc, slideNos]);
  const reading = open && !!doc && !!slideNos && slides === null;

  const hits = useMemo(
    () =>
      open
        ? searchLesson(
            pages.map((p) => ({ id: p.id, notes: p.notes, ink: p.ink, slide: (p.kind === "slide" && p.slide_page && slides?.get(p.slide_page)) || null })),
            query,
          )
        : [],
    [open, pages, slides, query],
  );
  const pagesRef = useRef(pages);
  pagesRef.current = pages;
  const hitsRef = useRef(hits);
  hitsRef.current = hits;
  const currentRef = useRef(current);
  currentRef.current = current;

  // A new query (or the slides just read) starts from the page being looked at; edits only keep the place.
  useEffect(() => {
    const i = firstFrom(hitsRef.current, pagesRef.current.map((p) => p.id), currentRef.current);
    setIndex(i);
    if (i >= 0) setSeek((n) => n + 1);
  }, [query, slides]);
  const at = hits.length ? Math.min(Math.max(index, 0), hits.length - 1) : -1;
  const hit = at >= 0 ? hits[at] : null;

  const step = useCallback((by: number) => {
    const n = hitsRef.current.length;
    if (!n) return;
    setIndex((i) => (((Math.min(Math.max(i, 0), n - 1) + by) % n) + n) % n);
    setSeek((s) => s + 1);
  }, []);
  /** Ctrl+F: opens the bar (with the words selected, if any) or brings the focus back to it. */
  const show = useCallback((selected?: string) => {
    if (selected) setQuery(selected);
    setOpen(true);
    setFocusNonce((n) => n + 1);
    setSeek((n) => n + 1);
  }, []);
  const close = useCallback(() => setOpen(false), []);

  const byPage = useMemo(() => {
    const m = new Map<number, PageFind>();
    for (const h of hits) {
      let f = m.get(h.pid);
      if (!f) m.set(h.pid, (f = { query, hits: [], current: null, seek }));
      f.hits.push(h);
      if (h === hit) f.current = h;
    }
    return m;
  }, [hits, hit, query, seek]);

  return { open, query, setQuery, hits, at, hit, reading, step, show, close, focusNonce, byPage };
}

/** The words selected in the page or in a field, to search for them (only a short piece of one line). */
export function selectedWords(): string {
  const el = document.activeElement;
  let s = "";
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) s = el.value.slice(el.selectionStart ?? 0, el.selectionEnd ?? 0);
  else s = window.getSelection()?.toString() ?? "";
  return s.length <= 120 && !s.includes("\n") ? s.trim() : "";
}

export function FindBar({
  find,
  where,
  onClose,
}: {
  find: ReturnType<typeof useLessonFind>;
  /** where the occurrence being looked at is (the page's name, and whether in the slide or the notes) */
  where: string | null;
  onClose: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, [find.focusNonce]);
  const { query, hits, at, reading } = find;
  const status = !foldQuery(query) ? "" : hits.length ? `${at + 1} / ${hits.length}` : reading ? "Leggo le slide…" : "Nessun risultato";
  return (
    <div className="les-find" role="search" data-testid="find-bar">
      <Icon name="search" />
      <input
        ref={input}
        type="search"
        className="les-find-input"
        value={query}
        placeholder="Cerca nelle slide e negli appunti"
        aria-label="Cerca nella lezione"
        data-testid="find-input"
        enterKeyHint="search"
        onChange={(e) => find.setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            find.step(e.shiftKey ? -1 : 1);
          } else if (e.key === "Escape") {
            e.preventDefault();
            onClose();
          }
        }}
      />
      <span className="les-find-count muted small mono" data-testid="find-count" aria-live="polite" title={where ?? undefined}>
        {status}
        {where && hits.length ? <span className="les-find-where"> · {where}</span> : null}
      </span>
      <div className="btn-group">
        <button type="button" className="btn icon" onClick={() => find.step(-1)} disabled={!hits.length} aria-label="Risultato precedente" title="Precedente (Maiusc+Invio)" data-testid="find-prev">
          <Icon name="chevron-up" />
        </button>
        <button type="button" className="btn icon" onClick={() => find.step(1)} disabled={!hits.length} aria-label="Risultato successivo" title="Successivo (Invio)" data-testid="find-next">
          <Icon name="chevron-down" />
        </button>
      </div>
      <button type="button" className="btn ghost icon" onClick={onClose} aria-label="Chiudi la ricerca" title="Chiudi (Esc)" data-testid="find-close">
        <Icon name="x" />
      </button>
    </div>
  );
}

// ------------------------------------------------------------------ on the pages

const box = (r: Rect, ratio: number) => ({
  left: `${r.x0 * 100}%`,
  top: `${(r.y0 / ratio) * 100}%`,
  width: `${(r.x1 - r.x0) * 100}%`,
  height: `${((r.y1 - r.y0) / ratio) * 100}%`,
});

/** The occurrences on the slide and on its typed text, over the slide (under the strokes). */
export function SlideMarks({ find, ratio }: { find: PageFind; ratio: number }) {
  const marks = find.hits.filter((h) => h.rects.length);
  if (!marks.length) return null;
  return (
    <div className="les-find-marks" aria-hidden>
      {marks.flatMap((h, i) =>
        h.rects.map((r, j) => (
          <span key={`${i}.${j}`} className={h === find.current ? "on" : undefined} data-find-current={h === find.current && j === 0 ? "" : undefined} style={box(r, ratio)} />
        )),
      )}
    </div>
  );
}

/** The occurrences in a page's notes: their ranges, and which one is being looked at (-1: none). */
export function noteMarks(find: PageFind | null): { ranges: Range[]; current: number } | null {
  if (!find) return null;
  const notes = find.hits.filter((h) => h.where === "notes");
  return notes.length ? { ranges: notes.map((h) => h.range!), current: notes.indexOf(find.current!) } : null;
}

// The rendered notes (the preview, a read-only link) are marked with the browser's highlights: their text is not the Markdown.
const shown = new Map<number, { all: globalThis.Range[]; current: globalThis.Range | null }>();
function paint() {
  if (typeof CSS === "undefined" || !("highlights" in CSS)) return;
  const all = [...shown.values()];
  CSS.highlights.set("lecta-find", new Highlight(...all.flatMap((s) => s.all)));
  CSS.highlights.set("lecta-find-current", new Highlight(...all.flatMap((s) => (s.current ? [s.current] : []))));
}

/** Marks the occurrences of the query in the rendered notes of a page; returns the one being looked at, to bring into view. */
export function useRenderedMarks(el: RefObject<HTMLElement | null>, pid: number, find: PageFind | null, on: boolean): RefObject<globalThis.Range | null> {
  const current = useRef<globalThis.Range | null>(null);
  const marks = on ? noteMarks(find) : null;
  const nth = marks?.current ?? -1;
  const q = marks ? foldQuery(find!.query) : "";
  useLayoutEffect(() => {
    current.current = null;
    const root = el.current;
    if (!root || !q) return;
    // The text of the rendered notes in one string (formulas left out), and the text node each piece comes from.
    const nodes: Text[] = [];
    const starts: number[] = [];
    let text = "";
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement?.closest(".md-math") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      nodes.push(n as Text);
      starts.push(text.length);
      text += n.nodeValue ?? "";
    }
    const point = (off: number, end: boolean): [Text, number] => {
      let k = starts.length - 1;
      while (k > 0 && (starts[k] > off || (end && starts[k] === off))) k--;
      return [nodes[k], off - starts[k]];
    };
    const ranges = findAll(text, q).map(([a, b]) => {
      const r = document.createRange();
      r.setStart(...point(a, false));
      r.setEnd(...point(b, true));
      return r;
    });
    current.current = nth >= 0 ? (ranges[Math.min(nth, ranges.length - 1)] ?? null) : null;
    shown.set(pid, { all: ranges, current: current.current });
    paint();
    return () => {
      shown.delete(pid);
      paint();
    };
    // The hits are new whenever the notes change: the rendered text is read again then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, nth, pid, find?.hits]);
  return current;
}

/** Scrolls the editor just enough to show a rectangle of the screen in its middle, unless it is already well in view. */
export function bringIntoView(scroller: HTMLElement, r: DOMRect) {
  const s = scroller.getBoundingClientRect();
  const margin = 40;
  const top = r.top < s.top + margin || r.bottom > s.bottom - margin ? r.top + r.height / 2 - (s.top + s.height / 2) : 0;
  const left = r.left < s.left || r.right > s.right ? r.left + r.width / 2 - (s.left + s.width / 2) : 0;
  if (top || left) scroller.scrollBy({ top, left, behavior: "smooth" });
}
