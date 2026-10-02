// Shape recognition for pen strokes, as in Xournal++: a stroke that is meant to be a line (an underline), a rectangle,
// a triangle or a circle/ellipse is replaced by the clean shape. Everything is in page widths, like the strokes.
// Self-contained on purpose (types only are imported), so it can be tested with plain node.
import type { Stroke } from "./ink";

type Pt = [number, number];

/** Shapes smaller than this (bounding box diagonal, in page widths) are handwriting, not drawings: left alone. */
const MIN_SHAPE = 0.05;
/** A line shorter than this is a dash, not an underline. */
const MIN_LINE = 0.05;
/** Mean distance of the stroke from the shape, relative to the shape's size, below which the stroke is taken for it. */
const MAX_ERROR = 0.03;
/** Lines this close to horizontal or vertical (radians) are made exactly so. */
const SNAP_ANGLE = (3 * Math.PI) / 180;
/** An ellipse whose axes differ by less than this (relative) becomes a circle. */
const CIRCLE_RATIO = 0.12;
/** Spacing of the points of the clean shape (page widths): dense enough that the smoothing of the drawing keeps corners sharp. */
const STEP = 0.004;

function points(s: Stroke): Pt[] {
  const out: Pt[] = [];
  for (let i = 0; i < s.p.length; i += 3) out.push([s.p[i], s.p[i + 1]]);
  return out;
}

function pathLength(p: Pt[]): number {
  let l = 0;
  for (let i = 1; i < p.length; i++) l += Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]);
  return l;
}

function distToSegment(p: Pt, a: Pt, b: Pt): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

/** Mean and maximum distance of the stroke's points from a polyline. */
function distances(p: Pt[], outline: Pt[]): { mean: number; max: number } {
  let sum = 0;
  let max = 0;
  for (const q of p) {
    let d = Infinity;
    for (let i = 1; i < outline.length; i++) d = Math.min(d, distToSegment(q, outline[i - 1], outline[i]));
    sum += d;
    max = Math.max(max, d);
  }
  return { mean: sum / p.length, max };
}

/** The points spread evenly along the path (so a slow part of the stroke doesn't weigh more than a fast one). */
function resample(p: Pt[], n: number): Pt[] {
  const total = pathLength(p);
  if (total === 0) return p;
  const out: Pt[] = [p[0]];
  const step = total / (n - 1);
  let acc = 0;
  let next = step;
  for (let i = 1; i < p.length; i++) {
    const seg = Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]);
    while (seg > 0 && acc + seg >= next && out.length < n) {
      const t = (next - acc) / seg;
      out.push([p[i - 1][0] + t * (p[i][0] - p[i - 1][0]), p[i - 1][1] + t * (p[i][1] - p[i - 1][1])]);
      next += step;
    }
    acc += seg;
  }
  if (out.length < n) out.push(p[p.length - 1]);
  return out;
}

/** Douglas–Peucker: the points that matter for the shape of the path, within eps. */
function simplify(p: Pt[], eps: number): Pt[] {
  if (p.length < 3) return p;
  let idx = 0;
  let dmax = 0;
  for (let i = 1; i < p.length - 1; i++) {
    const d = distToSegment(p[i], p[0], p[p.length - 1]);
    if (d > dmax) {
      dmax = d;
      idx = i;
    }
  }
  if (dmax <= eps) return [p[0], p[p.length - 1]];
  const left = simplify(p.slice(0, idx + 1), eps);
  return [...left.slice(0, -1), ...simplify(p.slice(idx), eps)];
}

/** Angle (radians, −π…π) at vertex b of the turn a→b→c: 0 = straight on. */
function turn(a: Pt, b: Pt, c: Pt): number {
  const t = Math.atan2(c[1] - b[1], c[0] - b[0]) - Math.atan2(b[1] - a[1], b[0] - a[0]);
  return Math.abs(Math.atan2(Math.sin(t), Math.cos(t)));
}

/** The corners of a closed path: simplified, with the vertices that are not really corners (and the start of the stroke) dropped. */
function corners(ring: Pt[], eps: number): Pt[] {
  let v = simplify(ring, eps);
  if (v.length > 1 && Math.hypot(v[0][0] - v[v.length - 1][0], v[0][1] - v[v.length - 1][1]) < eps * 2) v = v.slice(0, -1);
  const limit = (25 * Math.PI) / 180;
  for (let again = true; again && v.length > 3; ) {
    again = false;
    for (let i = 0; i < v.length; i++) {
      if (turn(v[(i + v.length - 1) % v.length], v[i], v[(i + 1) % v.length]) < limit) {
        v = v.filter((_, j) => j !== i);
        again = true;
        break;
      }
    }
  }
  return v;
}

function rotate(p: Pt, theta: number, about: Pt = [0, 0]): Pt {
  const c = Math.cos(theta);
  const s = Math.sin(theta);
  const x = p[0] - about[0];
  const y = p[1] - about[1];
  return [about[0] + x * c - y * s, about[1] + x * s + y * c];
}

/** Points along a polyline every STEP, corners included (so the drawn corner stays sharp). */
function densify(poly: Pt[]): Pt[] {
  const out: Pt[] = [poly[0]];
  for (let i = 1; i < poly.length; i++) {
    const a = poly[i - 1];
    const b = poly[i];
    const n = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / STEP));
    for (let k = 1; k <= n; k++) out.push([a[0] + ((b[0] - a[0]) * k) / n, a[1] + ((b[1] - a[1]) * k) / n]);
  }
  return out;
}

interface Candidate {
  poly: Pt[]; // the outline, as a polyline (what the new stroke follows)
  error: number; // mean distance of the stroke from it, relative to its size
  circle?: boolean;
}

/** Sides and axes that are nearly level or plumb are made exactly so. */
function snapAxis(theta: number): number {
  const near = Math.round(theta / (Math.PI / 2)) * (Math.PI / 2);
  return Math.abs(theta - near) <= (5 * Math.PI) / 180 ? near : theta;
}

/** The smallest rectangle around the points with sides at angle theta. */
function rectangleAt(p: Pt[], angle: number, diag: number): Candidate {
  const theta = snapAxis(angle);
  const r = p.map((q) => rotate(q, -theta));
  const x0 = Math.min(...r.map((q) => q[0]));
  const x1 = Math.max(...r.map((q) => q[0]));
  const y0 = Math.min(...r.map((q) => q[1]));
  const y1 = Math.max(...r.map((q) => q[1]));
  const poly = ([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]] as Pt[]).map((q) => rotate(q, theta));
  return { poly, error: distances(p, poly).mean / diag };
}

/** The ellipse with axes at angle theta that encloses the points' extent; a near circle becomes a circle. */
function ellipseAt(p: Pt[], angle: number, diag: number): Candidate {
  const theta = snapAxis(angle);
  const r = p.map((q) => rotate(q, -theta));
  const x0 = Math.min(...r.map((q) => q[0]));
  const x1 = Math.max(...r.map((q) => q[0]));
  const y0 = Math.min(...r.map((q) => q[1]));
  const y1 = Math.max(...r.map((q) => q[1]));
  let a = (x1 - x0) / 2;
  let b = (y1 - y0) / 2;
  const circle = Math.abs(a - b) / Math.max(a, b) < CIRCLE_RATIO;
  if (circle) a = b = (a + b) / 2;
  const c: Pt = [(x0 + x1) / 2, (y0 + y1) / 2];
  const n = Math.max(48, Math.ceil((2 * Math.PI * Math.max(a, b)) / STEP));
  const poly: Pt[] = [];
  for (let i = 0; i <= n; i++) {
    const t = (2 * Math.PI * i) / n;
    poly.push(rotate([c[0] + a * Math.cos(t), c[1] + b * Math.sin(t)], theta));
  }
  return { poly, error: distances(p, poly).mean / diag, circle };
}

/** Direction of the main axis of the points (radians), from their spread. */
function principalAngle(p: Pt[]): number {
  const n = p.length;
  const mx = p.reduce((s, q) => s + q[0], 0) / n;
  const my = p.reduce((s, q) => s + q[1], 0) / n;
  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  for (const q of p) {
    sxx += (q[0] - mx) ** 2;
    syy += (q[1] - my) ** 2;
    sxy += (q[0] - mx) * (q[1] - my);
  }
  return 0.5 * Math.atan2(2 * sxy, sxx - syy);
}

/** Orientation of the sides of a quadrilateral-ish path: the mean direction of its corners' edges, folded to a quarter turn. */
function sideAngle(v: Pt[]): number {
  let sx = 0;
  let sy = 0;
  for (let i = 0; i < v.length; i++) {
    const a = v[i];
    const b = v[(i + 1) % v.length];
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
    sx += len * Math.cos(4 * ang);
    sy += len * Math.sin(4 * ang);
  }
  return Math.atan2(sy, sx) / 4;
}

function lineCandidate(p: Pt[]): Pt[] | null {
  const a = p[0];
  const b = p[p.length - 1];
  const chord = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const len = pathLength(p);
  if (chord < MIN_LINE || chord / len < 0.85) return null;
  const { mean, max } = distances(p, [a, b]);
  if (mean > 0.02 * chord || max > 0.07 * chord) return null;
  // Horizontal and vertical lines (underlines, mostly) are made exactly so, around their middle.
  const mid: Pt = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
  const near = Math.round(ang / (Math.PI / 2)) * (Math.PI / 2);
  const theta = Math.abs(ang - near) <= SNAP_ANGLE ? near : ang;
  const half = chord / 2;
  return [
    [mid[0] - half * Math.cos(theta), mid[1] - half * Math.sin(theta)],
    [mid[0] + half * Math.cos(theta), mid[1] + half * Math.sin(theta)],
  ];
}

/** The clean shape a stroke was meant to be, or null when it is just a scribble (or handwriting). */
export function recognize(s: Stroke): Stroke | null {
  const p = points(s);
  if (p.length < 6) return null;
  const len = pathLength(p);
  const xs = p.map((q) => q[0]);
  const ys = p.map((q) => q[1]);
  const w = Math.max(...xs) - Math.min(...xs);
  const h = Math.max(...ys) - Math.min(...ys);
  const diag = Math.hypot(w, h);
  const gap = Math.hypot(p[0][0] - p[p.length - 1][0], p[0][1] - p[p.length - 1][1]);

  let poly: Pt[] | null = null;
  if (gap > 0.35 * len) {
    poly = lineCandidate(p);
  } else if (diag >= MIN_SHAPE && len > 1.6 * diag && gap < 0.25 * diag) {
    // Closed: a triangle, a rectangle or an ellipse, whichever the stroke follows best.
    const ring = resample([...p, p[0]], 96);
    const v = corners(ring, 0.06 * diag);
    const all: Candidate[] = [];
    if (v.length === 3) {
      const tri = [...v, v[0]];
      all.push({ poly: tri, error: distances(p, tri).mean / diag });
    }
    for (const theta of new Set([0, sideAngle(v)])) all.push(rectangleAt(p, theta, diag));
    const rs = resample(p, 64);
    for (const theta of new Set([0, principalAngle(rs)])) all.push(ellipseAt(p, theta, diag));
    // Whichever outline the stroke hugs most wins (a circle is far from any rectangle, and the other way round).
    const best = all.reduce((m, c) => (c.error < m.error ? c : m));
    if (best.error <= MAX_ERROR) poly = best.poly;
  }
  if (!poly) return null;
  const dense = poly.length === 2 ? poly : densify(poly);
  return { ...s, p: dense.flatMap(([x, y]) => [Math.round(x * 10000) / 10000, Math.round(y * 10000) / 10000, 0.5]) };
}
