// Shape recognition: run with `npm test` (node's own runner; the TypeScript is stripped by node itself).
import assert from "node:assert/strict";
import { test } from "node:test";
import { recognize } from "../src/components/lessons/shapes.ts";

let seed = 7;
const rnd = () => {
  seed = (seed * 16807) % 2147483647;
  return seed / 2147483647 - 0.5;
};
const stroke = (pts) => ({ t: "pen", c: "#000000", w: 0.003, p: pts.flatMap(([x, y]) => [x, y, 0.5]) });
const jitter = (pts, amp) => pts.map(([x, y]) => [x + rnd() * amp, y + rnd() * amp]);
/** Points along a polyline, n per unit of length (a hand draws at about this density). */
function along(poly, step = 0.006) {
  const out = [poly[0]];
  for (let i = 1; i < poly.length; i++) {
    const [a, b] = [poly[i - 1], poly[i]];
    const n = Math.max(1, Math.round(Math.hypot(b[0] - a[0], b[1] - a[1]) / step));
    for (let k = 1; k <= n; k++) out.push([a[0] + ((b[0] - a[0]) * k) / n, a[1] + ((b[1] - a[1]) * k) / n]);
  }
  return out;
}
const pts = (s) => Array.from({ length: s.p.length / 3 }, (_, i) => [s.p[i * 3], s.p[i * 3 + 1]]);
const box = (s) => {
  const q = pts(s);
  return { x0: Math.min(...q.map((a) => a[0])), x1: Math.max(...q.map((a) => a[0])), y0: Math.min(...q.map((a) => a[1])), y1: Math.max(...q.map((a) => a[1])) };
};
const ellipse = (cx, cy, a, b, turns = 1.03, start = 0.7) =>
  Array.from({ length: Math.round(120 * turns) }, (_, i) => {
    const t = start + (2 * Math.PI * turns * i) / Math.round(120 * turns);
    return [cx + a * Math.cos(t), cy + b * Math.sin(t)];
  });

test("a slightly crooked underline becomes a level line", () => {
  const r = recognize(stroke(jitter(along([[0.1, 0.30], [0.5, 0.312]]), 0.002)));
  assert.ok(r);
  const q = pts(r);
  assert.equal(q.length, 2);
  assert.equal(q[0][1], q[1][1]);
  assert.ok(q[1][0] - q[0][0] > 0.38);
});

test("a slanted line stays slanted, but straight", () => {
  const r = recognize(stroke(jitter(along([[0.1, 0.1], [0.4, 0.3]]), 0.002)));
  assert.ok(r);
  const q = pts(r);
  assert.equal(q.length, 2);
  assert.ok(Math.abs(Math.atan2(q[1][1] - q[0][1], q[1][0] - q[0][0]) - Math.atan2(0.2, 0.3)) < 0.05);
});

test("a hand-drawn rectangle becomes an exact one", () => {
  const r = recognize(stroke(jitter(along([[0.2, 0.2], [0.6, 0.21], [0.61, 0.5], [0.19, 0.49], [0.2, 0.2]]), 0.004)));
  assert.ok(r);
  const b = box(r);
  assert.ok(Math.abs(b.x1 - b.x0 - 0.41) < 0.04 && Math.abs(b.y1 - b.y0 - 0.29) < 0.04);
  // Every point lies on one of the four sides.
  for (const [x, y] of pts(r)) {
    const onSide = Math.min(Math.abs(x - b.x0), Math.abs(x - b.x1), Math.abs(y - b.y0), Math.abs(y - b.y1));
    assert.ok(onSide < 1e-3, `${x},${y} off the sides`);
  }
});

test("a rectangle started mid-edge and left open at the end is still a rectangle", () => {
  const r = recognize(stroke(jitter(along([[0.4, 0.2], [0.6, 0.2], [0.6, 0.5], [0.2, 0.5], [0.2, 0.2], [0.38, 0.2]]), 0.003)));
  assert.ok(r);
  const b = box(r);
  assert.ok(Math.abs(b.x1 - b.x0 - 0.4) < 0.03 && Math.abs(b.y1 - b.y0 - 0.3) < 0.03);
});

test("a tilted rectangle keeps its tilt", () => {
  const th = 0.4;
  const rot = ([x, y]) => [0.5 + (x - 0.5) * Math.cos(th) - (y - 0.4) * Math.sin(th), 0.4 + (x - 0.5) * Math.sin(th) + (y - 0.4) * Math.cos(th)];
  const r = recognize(stroke(jitter(along([[0.3, 0.3], [0.7, 0.3], [0.7, 0.5], [0.3, 0.5], [0.3, 0.3]].map(rot)), 0.003)));
  assert.ok(r);
  const b = box(r);
  assert.ok(b.x1 - b.x0 > 0.36 && b.y1 - b.y0 > 0.3, "the bounding box of a tilted rectangle is bigger than the rectangle");
});

test("a circle becomes a circle, an ellipse an ellipse", () => {
  const c = recognize(stroke(jitter(ellipse(0.5, 0.5, 0.15, 0.15), 0.006)));
  assert.ok(c);
  const radii = pts(c).map(([x, y]) => Math.hypot(x - 0.5, y - 0.5));
  assert.ok(Math.max(...radii) - Math.min(...radii) < 0.01, "all points at the same distance from the centre");
  const e = recognize(stroke(jitter(ellipse(0.5, 0.5, 0.25, 0.1), 0.004)));
  assert.ok(e);
  const b = box(e);
  assert.ok(Math.abs(b.x1 - b.x0 - 0.5) < 0.03 && Math.abs(b.y1 - b.y0 - 0.2) < 0.03);
});

test("a triangle becomes a triangle", () => {
  const r = recognize(stroke(jitter(along([[0.5, 0.2], [0.75, 0.6], [0.25, 0.6], [0.5, 0.2]]), 0.004)));
  assert.ok(r);
  const b = box(r);
  assert.ok(Math.abs(b.y0 - 0.2) < 0.03 && Math.abs(b.y1 - 0.6) < 0.03);
  // The top corner is a corner (the apex is at the extreme; a circle or rectangle fit would not have a point there).
  const apex = pts(r).filter(([, y]) => y < 0.205);
  assert.ok(apex.length >= 1 && apex.length < 6);
});

test("handwriting and scribbles are left alone", () => {
  assert.equal(recognize(stroke(ellipse(0.5, 0.5, 0.008, 0.01))), null, "a small o");
  assert.equal(recognize(stroke(along([[0.1, 0.1], [0.12, 0.1]]))), null, "a dash");
  const zig = along([[0.1, 0.1], [0.2, 0.3], [0.3, 0.1], [0.4, 0.3], [0.5, 0.1], [0.6, 0.3]]);
  assert.equal(recognize(stroke(zig)), null, "a zigzag");
  const wave = Array.from({ length: 80 }, (_, i) => [0.1 + i * 0.006, 0.3 + 0.03 * Math.sin(i / 4)]);
  assert.equal(recognize(stroke(wave)), null, "a wavy underline");
  const arc = Array.from({ length: 60 }, (_, i) => [0.3 + 0.2 * Math.cos(i / 20), 0.4 + 0.2 * Math.sin(i / 20)]);
  assert.equal(recognize(stroke(arc)), null, "an open arc");
});

test("the recognised stroke keeps its pen, colour and width", () => {
  const r = recognize({ ...stroke(jitter(along([[0.1, 0.3], [0.5, 0.3]]), 0.001)), t: "hl", c: "#ffeb3b", w: 0.026 });
  assert.ok(r && r.t === "hl" && r.c === "#ffeb3b" && r.w === 0.026);
});
