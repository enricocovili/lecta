// Ink: the pen strokes of a lesson page. Coordinates are in units of the page's width (x and y alike), so the
// same numbers fit every zoom and screen, and match what the server stores and draws on the annotated PDF.

export type Tool = "pen" | "hl" | "eraser" | "hand";

export interface Stroke {
  /** pen | hl (highlighter) */
  t: "pen" | "hl";
  /** #rrggbb */
  c: string;
  /** stroke width, in page widths */
  w: number;
  /** x, y, pressure, x, y, pressure, … */
  p: number[];
}

export const HL_ALPHA = 0.35;
export const PEN_WIDTHS = [0.0016, 0.0032, 0.0065];
export const HL_WIDTHS = [0.014, 0.026];
export const ERASER_RADIUS = 0.011;
export const COLORS = ["#1c1b17", "#1e4fd8", "#d32f2f", "#2e7d32", "#ef6c00", "#7b1fa2"];
export const HL_COLORS = ["#ffeb3b", "#69f0ae", "#ff80ab", "#80d8ff"];
/** Points closer than this (in page widths) to the previous one are dropped. */
const MIN_STEP = 0.0005;

export function roundStroke(s: Stroke): Stroke {
  return { ...s, p: s.p.map((v, i) => (i % 3 === 2 ? Math.round(v * 100) / 100 : Math.round(v * 10000) / 10000)) };
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

/** Redraw everything (highlighters first, so pens stay readable above them). */
export function drawAll(ctx: CanvasRenderingContext2D, strokes: Stroke[], scale: number, skip?: Set<number>): void {
  for (const kind of ["hl", "pen"] as const) {
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

/** The rectangle around a stroke's points (kept: the eraser asks for it at every move, for every stroke). */
function boxOf(s: Stroke): Box {
  let b = boxes.get(s);
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
  const pad = s.w;
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
  for (const kind of ["hl", "pen"] as const) {
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
  const reach = r + s.w / 2;
  const b = boxOf(s);
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
