// Searching a lesson (Ctrl+F): the text printed on the slides, the typed notes and the text typed on the pages. Capitals,
// accents and runs of spaces do not count ("perche" finds "Perché", "a b" finds "a\n  b"). Run the tests with `npm test`.
import { lineWidth, strokeRect, TEXT_LINE_HEIGHT, textLines, type Rect, type Stroke } from "./ink.ts";

/** A range of a text, [start, end) in its UTF-16 offsets. */
export type Range = [number, number];

interface Folded {
  text: string;
  /** for every char of `text`, where it starts and ends in the original */
  from: number[];
  to: number[];
}

/** The text as it is compared: lower case, accents and ligatures taken apart (only the letters kept), every run of
 *  whitespace one space; with where each char comes from. */
export function fold(s: string): Folded {
  const out: Folded = { text: "", from: [], to: [] };
  let i = 0;
  for (const ch of s) {
    const end = i + ch.length;
    if (/\s/.test(ch)) {
      if (out.text.endsWith(" ")) out.to[out.to.length - 1] = end;
      else {
        out.text += " ";
        out.from.push(i);
        out.to.push(end);
      }
    } else {
      for (const c of ch.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase()) {
        out.text += c;
        out.from.push(i);
        out.to.push(end);
      }
    }
    i = end;
  }
  return out;
}

/** The query as it is compared; empty when there is nothing to look for. */
export function foldQuery(q: string): string {
  const f = fold(q).text;
  return f.trim() ? f : "";
}

/** Where the (folded) query occurs in a text, one after the other without overlapping. */
export function findAll(text: string, query: string): Range[] {
  if (!query) return [];
  const f = fold(text);
  const out: Range[] = [];
  for (let at = f.text.indexOf(query); at >= 0; at = f.text.indexOf(query, at + query.length)) {
    out.push([f.from[at], f.to[at + query.length - 1]]);
  }
  return out;
}

// ------------------------------------------------------------------ slides

/** A piece of text of a slide, as pdf.js gives it: its string, its matrix (font size and place, in PDF points with y up), its
 *  width and whether a line ends after it. */
export interface SlideItem {
  str: string;
  transform: number[];
  width: number;
  hasEOL?: boolean;
}

/** The text of a slide in one string (pieces one after the other, a space where a line ends, as pdf.js' own search does),
 *  with where each piece starts. */
export function slideText(items: SlideItem[]): { text: string; starts: number[] } {
  let text = "";
  const starts: number[] = [];
  for (const it of items) {
    starts.push(text.length);
    text += it.str;
    if (it.hasEOL) text += " ";
  }
  return { text, starts };
}

/** The boxes (in page widths, from the page's top-left corner) of a range of the slide's text: one per piece it covers, its
 *  share of the piece's width counted by characters. `toPage` turns a point of the PDF into one of the page. */
export function slideRects(items: SlideItem[], starts: number[], [a, b]: Range, toPage: (x: number, y: number) => [number, number]): Rect[] {
  const rects: Rect[] = [];
  items.forEach((it, k) => {
    const s = starts[k];
    const len = it.str.length;
    const i0 = Math.max(a, s) - s;
    const i1 = Math.min(b, s + len) - s;
    if (!len || i1 <= i0) return;
    const [ma, mb, mc, md, e, f] = it.transform;
    const size = Math.hypot(mc, md) || Math.hypot(ma, mb);
    const dir = Math.hypot(ma, mb) ? [ma / Math.hypot(ma, mb), mb / Math.hypot(ma, mb)] : [1, 0];
    const along0 = (it.width * i0) / len;
    const along1 = (it.width * i1) / len;
    // The four corners: along the baseline, from a bit under it (descenders) to the top of the letters.
    const corners = [
      [along0, -0.22 * size],
      [along1, -0.22 * size],
      [along0, 0.9 * size],
      [along1, 0.9 * size],
    ].map(([u, v]) => toPage(e + u * dir[0] - v * dir[1], f + u * dir[1] + v * dir[0]));
    const xs = corners.map((c) => c[0]);
    const ys = corners.map((c) => c[1]);
    rects.push({ x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) });
  });
  return rects;
}

// ------------------------------------------------------------------ typed text on the page

/** The boxes (page widths) of a range of a typed text: one per line it covers. A stretched or turned text gets its whole box. */
export function inkTextRects(s: Stroke, [a, b]: Range): Rect[] {
  if (s.m) return [strokeRect(s)];
  const rects: Rect[] = [];
  let start = 0;
  textLines(s).forEach((line, n) => {
    const i0 = Math.max(a, start) - start;
    const i1 = Math.min(b, start + line.length) - start;
    if (i1 > i0) {
      const top = s.p[1] + n * TEXT_LINE_HEIGHT * s.w;
      rects.push({
        x0: s.p[0] + lineWidth(line.slice(0, i0)) * s.w,
        y0: top,
        x1: s.p[0] + lineWidth(line.slice(0, i1)) * s.w,
        y1: top + TEXT_LINE_HEIGHT * s.w,
      });
    }
    start += line.length + 1;
  });
  return rects;
}

// ------------------------------------------------------------------ the whole lesson

/** One occurrence: on the slide or on the typed text of a page (`rects`, page widths), or in its notes (`range`). */
export interface Hit {
  pid: number;
  where: "slide" | "ink" | "notes";
  rects: Rect[];
  range: Range | null;
}

/** The text of a slide, and how a point of its PDF lands on the page (in page widths). */
export interface SlideText {
  items: SlideItem[];
  toPage: (x: number, y: number) => [number, number];
}

export interface SearchPage {
  id: number;
  notes: string;
  ink: Stroke[];
  /** the text of its slide, when it has one and it has been read */
  slide: SlideText | null;
}

/** Every occurrence in the lesson, in the order of the pages; in a page the slide's, then the typed text's, then the notes'. */
export function searchLesson(pages: SearchPage[], query: string): Hit[] {
  const q = foldQuery(query);
  if (!q) return [];
  const hits: Hit[] = [];
  for (const p of pages) {
    if (p.slide) {
      const { text, starts } = slideText(p.slide.items);
      for (const r of findAll(text, q)) hits.push({ pid: p.id, where: "slide", rects: slideRects(p.slide.items, starts, r, p.slide.toPage), range: null });
    }
    for (const s of p.ink) {
      if (s.t !== "text" || !s.s) continue;
      for (const r of findAll(s.s, q)) hits.push({ pid: p.id, where: "ink", rects: inkTextRects(s, r), range: null });
    }
    for (const r of findAll(p.notes, q)) hits.push({ pid: p.id, where: "notes", rects: [], range: r });
  }
  return hits;
}

/** The hit to start from: the first one on the page being looked at or after it (the first of all past the last page). */
export function firstFrom(hits: Hit[], pageIds: number[], current: number): number {
  if (!hits.length) return -1;
  const order = new Map(pageIds.map((id, i) => [id, i]));
  const i = hits.findIndex((h) => (order.get(h.pid) ?? 0) >= current);
  return i < 0 ? 0 : i;
}
