// Ink: the pen strokes of a lesson page, and the typed text written on it (an item of the same list, so that it is selected,
// moved, scaled, erased and undone like a stroke). Coordinates are in units of the page's width (x and y alike), so the
// same numbers fit every zoom and screen, and match what the server stores and draws on the annotated PDF.

export type Tool = "pen" | "hl" | "text" | "eraser" | "select" | "hand";

export interface Stroke {
  /** pen | hl (highlighter) | text (typed) */
  t: "pen" | "hl" | "text";
  /** #rrggbb */
  c: string;
  /** stroke width, in page widths; for a text, its font size */
  w: number;
  /** x, y, pressure, x, y, pressure, …; for a text, its top-left corner and 0 */
  p: number[];
  /** text only: what is written, lines separated by \n */
  s?: string;
  /** text only: how its lines are stretched (and turned) around its top-left corner, as a canvas' a, b, c, d; none when
   *  they are upright and unstretched. Scaled to area 1: the size is the font size's. */
  m?: [number, number, number, number];
}

export const HL_ALPHA = 0.35;
export const PEN_WIDTHS = [0.0016, 0.0032, 0.0065];
export const HL_WIDTHS = [0.014, 0.026];
export const ERASER_RADIUS = 0.011;
export const COLORS = ["#1c1b17", "#1e4fd8", "#d32f2f", "#2e7d32", "#ef6c00", "#7b1fa2"];
export const HL_COLORS = ["#ffeb3b", "#69f0ae", "#ff80ab", "#80d8ff"];
/** Font sizes of typed text, in page widths (18, 26 and 38 px on a slide 1000 px wide). */
export const TEXT_SIZES = [0.018, 0.026, 0.038];
/** Typed text: lines this many font sizes apart, in a font the server's PDF has too (Helvetica). */
export const TEXT_LINE_HEIGHT = 1.25;
export const TEXT_FONT = 'Helvetica, Arial, "Liberation Sans", sans-serif';

let measurer: CanvasRenderingContext2D | null | undefined;
function measuring(): CanvasRenderingContext2D | null {
  if (measurer === undefined) measurer = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d");
  if (measurer) measurer.font = `100px ${TEXT_FONT}`;
  return measurer;
}

/** The width of a line of typed text at font size 1 (an estimate where there is no canvas to measure it). */
export function lineWidth(line: string): number {
  const m = measuring();
  return m ? m.measureText(line).width / 100 : line.length * 0.55;
}

let baseline: number | undefined;
/** Where the first baseline of a text is below its top, in font sizes: where a text field with the same line height puts it
 *  (half the leading, then the font's ascent), so the text stays put when its field is closed. The server uses 0.97, which is
 *  this for Helvetica and Arial. */
export function textBaseline(): number {
  if (baseline === undefined) {
    const m = measuring()?.measureText("Hg");
    const a = m?.fontBoundingBoxAscent;
    const d = m?.fontBoundingBoxDescent;
    baseline = a && d ? (TEXT_LINE_HEIGHT * 100 - (a + d)) / 2 / 100 + a / 100 : 0.97;
  }
  return baseline;
}

export function textLines(s: Stroke): string[] {
  return (s.s ?? "").split("\n");
}

/** A canvas never gets more pixels than this: zoomed in, a slide would otherwise take hundreds of MB. */
const MAX_CANVAS_PX = 8_000_000;

/** Canvas pixels per CSS pixel for a page of this size: the screen's density (at most 2), less when the page is huge. */
export function canvasScale(width: number, height: number): number {
  const dpr = typeof window === "undefined" ? 1 : Math.min(window.devicePixelRatio || 1, 2);
  return Math.min(dpr, Math.sqrt(MAX_CANVAS_PX / Math.max(1, width * height)));
}

/** Points closer than this (in page widths) to the previous one are dropped. */
const MIN_STEP = 0.0005;

export function roundStroke(s: Stroke): Stroke {
  const r = (v: number) => Math.round(v * 10000) / 10000;
  const out = { ...s, p: s.p.map((v, i) => (i % 3 === 2 ? Math.round(v * 100) / 100 : r(v))) };
  if (s.m) out.m = s.m.map(r) as Stroke["m"];
  return out;
}

/** Whether a new point is far enough from the last one to be worth keeping. */
export function farEnough(p: number[], x: number, y: number): boolean {
  const n = p.length;
  if (n < 3) return true;
  return Math.hypot(x - p[n - 3], y - p[n - 2]) >= MIN_STEP;
}

/** Pressure varies enough to be drawn with a varying width (a mouse always reports the same). */
function variable(p: number[]): boolean {
  let lo = 1;
  let hi = 0;
  for (let i = 2; i < p.length; i += 3) {
    lo = Math.min(lo, p[i]);
    hi = Math.max(hi, p[i]);
  }
  return hi - lo > 0.08;
}

/** Draw one stroke. `scale` = canvas pixels per page width. */
export function drawStroke(ctx: CanvasRenderingContext2D, s: Stroke, scale: number): void {
  const n = s.p.length / 3;
  if (n === 0) return;
  if (s.t === "text") {
    ctx.save();
    ctx.fillStyle = s.c;
    ctx.font = `${s.w * scale}px ${TEXT_FONT}`;
    ctx.textBaseline = "alphabetic";
    const top = textBaseline();
    ctx.translate(s.p[0] * scale, s.p[1] * scale);
    if (s.m) ctx.transform(s.m[0], s.m[1], s.m[2], s.m[3], 0, 0);
    textLines(s).forEach((line, i) => ctx.fillText(line, 0, (top + i * TEXT_LINE_HEIGHT) * s.w * scale));
    ctx.restore();
    return;
  }
  const base = Math.max(0.6, s.w * scale);
  ctx.save();
  ctx.strokeStyle = s.c;
  ctx.fillStyle = s.c;
  ctx.lineJoin = "round";
  ctx.lineCap = s.t === "hl" ? "butt" : "round";
  if (s.t === "hl") ctx.globalAlpha = HL_ALPHA;
  const X = (i: number) => s.p[i * 3] * scale;
  const Y = (i: number) => s.p[i * 3 + 1] * scale;
  if (n === 1) {
    ctx.beginPath();
    ctx.arc(X(0), Y(0), base / 2, 0, Math.PI * 2);
    ctx.fill();
  } else if (s.t === "pen" && variable(s.p)) {
    // Segment by segment, each with the width its pressure asks for (same curve as the constant-width path).
    const mx = (i: number) => (X(i) + X(i + 1)) / 2;
    const my = (i: number) => (Y(i) + Y(i + 1)) / 2;
    for (let i = 1; i < n - 1; i++) {
      ctx.lineWidth = base * (0.35 + 1.3 * s.p[i * 3 + 2]);
      ctx.beginPath();
      ctx.moveTo(i === 1 ? X(0) : mx(i - 1), i === 1 ? Y(0) : my(i - 1));
      ctx.quadraticCurveTo(X(i), Y(i), mx(i), my(i));
      ctx.stroke();
    }
    ctx.lineWidth = base * (0.35 + 1.3 * s.p[(n - 1) * 3 + 2]);
    ctx.beginPath();
    ctx.moveTo(n === 2 ? X(0) : mx(n - 2), n === 2 ? Y(0) : my(n - 2));
    ctx.lineTo(X(n - 1), Y(n - 1));
    ctx.stroke();
  } else {
    ctx.lineWidth = base;
    ctx.beginPath();
    ctx.moveTo(X(0), Y(0));
    for (let i = 1; i < n - 1; i++) ctx.quadraticCurveTo(X(i), Y(i), (X(i) + X(i + 1)) / 2, (Y(i) + Y(i + 1)) / 2);
    ctx.lineTo(X(n - 1), Y(n - 1));
    ctx.stroke();
  }
  ctx.restore();
}

/** Drawn in this order: highlighters first, so pens stay readable above them, and typed text on top. */
const LAYERS = ["hl", "pen", "text"] as const;

/** Redraw everything, layer by layer. */
export function drawAll(ctx: CanvasRenderingContext2D, strokes: Stroke[], scale: number, skip?: Set<number>): void {
  for (const kind of LAYERS) {
    strokes.forEach((s, i) => {
      if (s.t === kind && !skip?.has(i)) drawStroke(ctx, s, scale);
    });
  }
}

interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  /** number of coordinates it was computed for */
  n: number;
}
const boxes = new WeakMap<Stroke, Box>();

/** A text's own size (its widest line, its lines' height), before its `m`. */
function textSize(s: Stroke): { w: number; h: number } {
  const lines = textLines(s);
  return { w: Math.max(...lines.map(lineWidth)) * s.w, h: lines.length * TEXT_LINE_HEIGHT * s.w };
}

/** The rectangle around a stroke's points, or around a text's lines (kept: the eraser asks for it at every move, for every
 *  stroke). */
function boxOf(s: Stroke): Box {
  let b = boxes.get(s);
  if (s.t === "text") {
    if (!b) {
      const { w, h } = textSize(s);
      const [x, y] = s.p;
      const [a, bb, c, d] = s.m ?? [1, 0, 0, 1];
      const xs = [0, a * w, c * h, a * w + c * h];
      const ys = [0, bb * w, d * h, bb * w + d * h];
      b = { x0: x + Math.min(...xs), y0: y + Math.min(...ys), x1: x + Math.max(...xs), y1: y + Math.max(...ys), n: s.p.length };
      boxes.set(s, b);
    }
    return b;
  }
  if (!b || s.p.length !== b.n) {
    b = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity, n: s.p.length };
    for (let i = 0; i < s.p.length; i += 3) {
      b.x0 = Math.min(b.x0, s.p[i]);
      b.x1 = Math.max(b.x1, s.p[i]);
      b.y0 = Math.min(b.y0, s.p[i + 1]);
      b.y1 = Math.max(b.y1, s.p[i + 1]);
    }
    boxes.set(s, b);
  }
  return b;
}

/** A rectangle in page widths. */
export interface Rect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** The rectangle a stroke covers, ink width included. */
export function strokeRect(s: Stroke): Rect {
  const b = boxOf(s);
  const pad = s.t === "text" ? s.w * 0.15 : s.w;
  return { x0: b.x0 - pad, y0: b.y0 - pad, x1: b.x1 + pad, y1: b.y1 + pad };
}

export function unionRect(a: Rect | null, b: Rect): Rect {
  return a ? { x0: Math.min(a.x0, b.x0), y0: Math.min(a.y0, b.y0), x1: Math.max(a.x1, b.x1), y1: Math.max(a.y1, b.y1) } : b;
}

/** Redraw only what lies inside `r`: clear it and draw the strokes that reach it. Erasing in a page with thousands of strokes
 *  would otherwise redraw all of them at every frame. */
export function drawRegion(ctx: CanvasRenderingContext2D, strokes: Stroke[], scale: number, r: Rect, skip?: Set<number>): void {
  const m = 2 / scale; // antialiasing at the edge
  const x0 = r.x0 - m;
  const y0 = r.y0 - m;
  const x1 = r.x1 + m;
  const y1 = r.y1 + m;
  ctx.save();
  ctx.beginPath();
  ctx.rect(x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale);
  ctx.clip();
  ctx.clearRect(x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale);
  for (const kind of LAYERS) {
    strokes.forEach((s, i) => {
      if (s.t !== kind || skip?.has(i)) return;
      const b = strokeRect(s);
      if (b.x1 < x0 || b.x0 > x1 || b.y1 < y0 || b.y0 > y1) return;
      drawStroke(ctx, s, scale);
    });
  }
  ctx.restore();
}

/** Whether the eraser (a disc of radius r at x, y) touches the stroke. */
export function hits(s: Stroke, x: number, y: number, r: number): boolean {
  const b = boxOf(s);
  if (s.t === "text") {
    if (!s.m) return x >= b.x0 - r && x <= b.x1 + r && y >= b.y0 - r && y <= b.y1 + r;
    // Turned or stretched: the point brought back into the text's own frame, against its upright rectangle.
    const [a, bb, c, d] = s.m;
    const det = a * d - bb * c || 1;
    const dx = x - s.p[0];
    const dy = y - s.p[1];
    const u = (d * dx - c * dy) / det;
    const v = (a * dy - bb * dx) / det;
    const { w, h } = textSize(s);
    return u >= -r && u <= w + r && v >= -r && v <= h + r;
  }
  const reach = r + s.w / 2;
  if (x < b.x0 - reach || x > b.x1 + reach || y < b.y0 - reach || y > b.y1 + reach) return false;
  const n = s.p.length / 3;
  if (n === 1) return Math.hypot(s.p[0] - x, s.p[1] - y) <= reach;
  for (let i = 1; i < n; i++) {
    if (distToSegment(x, y, s.p[(i - 1) * 3], s.p[(i - 1) * 3 + 1], s.p[i * 3], s.p[i * 3 + 1]) <= reach) return true;
  }
  return false;
}

function distToSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

// ------------------------------------------------------------------ selection

/** How close (page widths) a click has to be to a stroke to pick it. */
export const PICK_RADIUS = 0.008;

/** The stroke a click at x, y picks (the one drawn on top: text, then pens, then highlighters, the latest first), or -1;
 *  `kinds` only those. */
export function pick(strokes: Stroke[], x: number, y: number, r = PICK_RADIUS, kinds: readonly Stroke["t"][] = ["text", "pen", "hl"]): number {
  for (const kind of kinds) {
    for (let i = strokes.length - 1; i >= 0; i--) if (strokes[i].t === kind && hits(strokes[i], x, y, r)) return i;
  }
  return -1;
}

/** Whether all of a stroke lies inside the rectangle (the selection rectangle takes what it encloses, like Xournal++). */
export function inside(s: Stroke, r: Rect): boolean {
  const b = boxOf(s);
  return b.x0 >= Math.min(r.x0, r.x1) && b.x1 <= Math.max(r.x0, r.x1) && b.y0 >= Math.min(r.y0, r.y1) && b.y1 <= Math.max(r.y0, r.y1);
}

/** The rectangle around some strokes (ink width included), or null for none. */
export function boundsOf(strokes: Stroke[]): Rect | null {
  return strokes.reduce<Rect | null>((acc, s) => unionRect(acc, strokeRect(s)), null);
}

/** The stroke moved by dx, dy (a new object: the history keeps the old one). */
export function moveStroke(s: Stroke, dx: number, dy: number): Stroke {
  return roundStroke({ ...s, p: s.p.map((v, i) => (i % 3 === 0 ? v + dx : i % 3 === 1 ? v + dy : v)) });
}

/** A corner of the selection's box, where a handle scales it from. */
export type Corner = "nw" | "ne" | "sw" | "se";
/** A side of the selection's box, where a handle stretches it in one direction only. */
export type Side = "n" | "s" | "e" | "w";
export type Handle = Corner | Side;
/** The smallest a selection can be scaled down to (its longer side, or the side stretched, in page widths), and how much
 *  bigger at most at once. */
const MIN_SELECTION = 0.01;
const MAX_SCALE = 10;
/** Stroke widths stay within what the server takes. */
const MIN_WIDTH = 0.0003;
const MAX_WIDTH = 0.2;

/** A map of the page onto itself, as a canvas' transform: x' = a x + c y + e, y' = b x + d y + f. */
export type Affine = [number, number, number, number, number, number];
export const IDENTITY: Affine = [1, 0, 0, 1, 0, 0];

/** Scaling by sx, sy around ax, ay. */
export function scaling(ax: number, ay: number, sx: number, sy: number): Affine {
  return [sx, 0, 0, sy, ax - ax * sx, ay - ay * sy];
}

export function applyAffine([a, b, c, d, e, f]: Affine, x: number, y: number): { x: number; y: number } {
  return { x: a * x + c * y + e, y: b * x + d * y + f };
}

/** Whether the map changes anything worth keeping. */
export function moves(t: Affine): boolean {
  return Math.max(Math.abs(t[0] - 1), Math.abs(t[1]), Math.abs(t[2]), Math.abs(t[3] - 1)) > 0.002 || Math.hypot(t[4], t[5]) > 0.0005;
}

/** The rectangle around the box once mapped. */
export function mapRect(t: Affine, r: Rect): Rect {
  const pts = [applyAffine(t, r.x0, r.y0), applyAffine(t, r.x1, r.y0), applyAffine(t, r.x0, r.y1), applyAffine(t, r.x1, r.y1)];
  return { x0: Math.min(...pts.map((p) => p.x)), y0: Math.min(...pts.map((p) => p.y)), x1: Math.max(...pts.map((p) => p.x)), y1: Math.max(...pts.map((p) => p.y)) };
}

/** The corner `c` of the box and the one opposite to it (which stays put while the box is scaled). */
export function cornerPoints(box: Rect, c: Corner): { cx: number; cy: number; ax: number; ay: number } {
  const west = c[1] === "w";
  const north = c[0] === "n";
  return { cx: west ? box.x0 : box.x1, cy: north ? box.y0 : box.y1, ax: west ? box.x1 : box.x0, ay: north ? box.y1 : box.y0 };
}

/** How many times the stretch from `a` (the side that stays) by `d` fits before `end` (the page's edge, 0 below `a`). */
const room = (a: number, d: number, end: number) => (d > 0 ? (end - a) / d : d < 0 ? a / -d : Infinity);

/** How much the box is scaled when its corner `c` is dragged to x, y: the drag along the box's diagonal, uniform (the strokes
 *  keep their proportions). Kept so that the box stays on the page (`ratio` = height / width; a box already past an edge is
 *  not shrunk for that) and does not vanish. */
export function scaleFactor(box: Rect, c: Corner, x: number, y: number, ratio: number): number {
  const { cx, cy, ax, ay } = cornerPoints(box, c);
  const dx = cx - ax;
  const dy = cy - ay;
  const len2 = dx * dx + dy * dy;
  if (!len2) return 1;
  const f = ((x - ax) * dx + (y - ay) * dy) / len2;
  const hi = Math.min(MAX_SCALE, Math.max(1, room(ax, dx, 1)), Math.max(1, room(ay, dy, ratio)));
  const lo = Math.min(1, MIN_SELECTION / Math.max(Math.abs(dx), Math.abs(dy)));
  return Math.min(hi, Math.max(lo, f));
}

/** The map that drags the handle `h` of the box to x, y: from a corner the box is scaled the same both ways around the
 *  opposite corner (`scaleFactor`), from a side only across it, around the opposite side (the proportions change). Kept on
 *  the page and from vanishing like a scaling, and never turned over: past the other side the box stays thin. */
export function stretchBy(box: Rect, h: Handle, x: number, y: number, ratio: number): Affine {
  if (h.length === 2) {
    const f = scaleFactor(box, h as Corner, x, y, ratio);
    const { ax, ay } = cornerPoints(box, h as Corner);
    return scaling(ax, ay, f, f);
  }
  const across = h === "e" || h === "w";
  const a = h === "e" ? box.x0 : h === "w" ? box.x1 : h === "s" ? box.y0 : box.y1;
  const d = (h === "e" ? box.x1 : h === "w" ? box.x0 : h === "s" ? box.y1 : box.y0) - a;
  if (!d) return IDENTITY;
  const hi = Math.min(MAX_SCALE, Math.max(1, room(a, d, across ? 1 : ratio)));
  const lo = Math.min(1, MIN_SELECTION / Math.abs(d));
  const f = Math.min(hi, Math.max(lo, ((across ? x : y) - a) / d));
  return across ? scaling(a, 0, f, 1) : scaling(0, a, 1, f);
}

/** The stroke mapped by t (a new object: the history keeps the old one): its points, not a picture of them, so it is drawn
 *  again as sharp as before at any size; its width (a text's font size) grows with the area. A text's corner moves with the
 *  map, and the rest of the map (a stretch, a turn) goes into its `m`. */
export function transformStroke(s: Stroke, t: Affine): Stroke {
  const [a, b, c, d, e, f] = t;
  const k = Math.sqrt(Math.abs(a * d - b * c)) || 1;
  const p = s.p.map((v, i, q) => (i % 3 === 0 ? a * v + c * q[i + 1] + e : i % 3 === 1 ? b * q[i - 1] + d * v + f : v));
  const { m: old, ...rest } = s;
  const out: Stroke = { ...rest, w: Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, s.w * k)), p };
  if (s.t === "text") {
    const [m0, m1, m2, m3] = old ?? [1, 0, 0, 1];
    const m: Stroke["m"] = [(a * m0 + c * m1) / k, (b * m0 + d * m1) / k, (a * m2 + c * m3) / k, (b * m2 + d * m3) / k];
    if (Math.max(Math.abs(m[0] - 1), Math.abs(m[1]), Math.abs(m[2]), Math.abs(m[3] - 1)) > 0.0005) out.m = m;
  }
  return roundStroke(out);
}

/** The move asked for, kept so that the box stays on the page (`ratio` = height / width); a box already past an edge is not
 *  pulled back, only kept from going further. */
export function clampShift(box: Rect, dx: number, dy: number, ratio: number): { dx: number; dy: number } {
  const lim = (d: number, lo: number, hi: number) => Math.min(Math.max(d, Math.min(0, lo)), Math.max(0, hi));
  return { dx: lim(dx, -box.x0, 1 - box.x1), dy: lim(dy, -box.y0, ratio - box.y1) };
}
