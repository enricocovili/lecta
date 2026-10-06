// The eraser redraws only the region it touched: run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { drawRegion, strokeRect, unionRect } from "../src/components/lessons/ink.ts";

const stroke = (x0, y0, x1, y1, t = "pen") => ({ t, c: "#000000", w: 0.003, p: [x0, y0, 0.5, x1, y1, 0.5] });

/** A canvas context that only counts what is stroked and cleared. */
function fakeCtx() {
  const log = { strokes: 0, cleared: [], clip: 0 };
  const noop = () => undefined;
  const ctx = new Proxy(
    { save: noop, restore: noop, beginPath: noop, moveTo: noop, lineTo: noop, quadraticCurveTo: noop, arc: noop, fill: noop, rect: noop },
    {
      get: (t, k) => {
        if (k === "stroke") return () => log.strokes++;
        if (k === "clip") return () => log.clip++;
        if (k === "clearRect") return (...a) => log.cleared.push(a);
        return t[k] ?? noop;
      },
      set: () => true,
    },
  );
  return { ctx, log };
}

test("only the strokes that reach the region are redrawn", () => {
  const strokes = [stroke(0.1, 0.1, 0.2, 0.1), stroke(0.1, 0.5, 0.2, 0.5), stroke(0.7, 0.9, 0.9, 0.9), stroke(0.15, 0.05, 0.15, 0.2, "hl")];
  const { ctx, log } = fakeCtx();
  drawRegion(ctx, strokes, 1000, { x0: 0.05, y0: 0.05, x1: 0.25, y1: 0.15 });
  assert.equal(log.strokes, 2, "the first stroke and the highlighter only");
  assert.equal(log.clip, 1);
  assert.equal(log.cleared.length, 1);
});

test("erased strokes are skipped and the region is the union of what was erased", () => {
  const strokes = [stroke(0.1, 0.1, 0.2, 0.1), stroke(0.12, 0.1, 0.22, 0.1)];
  const r = unionRect(unionRect(null, strokeRect(strokes[0])), strokeRect(strokes[1]));
  assert.ok(r.x0 < 0.1 && r.x1 > 0.22);
  const { ctx, log } = fakeCtx();
  drawRegion(ctx, strokes, 1000, r, new Set([0]));
  assert.equal(log.strokes, 1);
});

// ------------------------------------------------------------------ selection

import { boundsOf, clampShift, inside, moveStroke, pick } from "../src/components/lessons/ink.ts";

test("a click picks the stroke under it, the one drawn on top when several are: pens over highlighters, the latest first", () => {
  const hl = stroke(0.1, 0.5, 0.9, 0.5, "hl");
  const first = stroke(0.1, 0.5, 0.9, 0.5);
  const latest = stroke(0.5, 0.2, 0.5, 0.8);
  const strokes = [first, latest, hl];
  assert.equal(pick(strokes, 0.5, 0.5), 1);
  assert.equal(pick(strokes, 0.2, 0.502), 0);
  assert.equal(pick([hl], 0.2, 0.5), 0);
  assert.equal(pick(strokes, 0.2, 0.7), -1);
});

test("the selection rectangle takes the strokes it encloses, drawn in any direction, not those it only crosses", () => {
  const small = stroke(0.2, 0.2, 0.3, 0.25);
  const long = stroke(0.2, 0.4, 0.9, 0.4);
  assert.equal(inside(small, { x0: 0.1, y0: 0.1, x1: 0.4, y1: 0.3 }), true);
  assert.equal(inside(small, { x0: 0.4, y0: 0.3, x1: 0.1, y1: 0.1 }), true);
  assert.equal(inside(long, { x0: 0.1, y0: 0.3, x1: 0.5, y1: 0.5 }), false);
  assert.equal(boundsOf([]), null);
  const b = boundsOf([small, long]);
  assert.ok(b.x0 < 0.2 && b.x1 > 0.9 && b.y0 < 0.2 && b.y1 > 0.4);
});

test("a moved stroke is a new one with the same look, every point shifted and the pressure untouched", () => {
  const s = { t: "pen", c: "#d32f2f", w: 0.003, p: [0.1, 0.2, 0.4, 0.3, 0.25, 0.9] };
  const m = moveStroke(s, 0.05, -0.1);
  assert.notEqual(m, s);
  assert.deepEqual(s.p, [0.1, 0.2, 0.4, 0.3, 0.25, 0.9]);
  assert.deepEqual(m, { t: "pen", c: "#d32f2f", w: 0.003, p: [0.15, 0.1, 0.4, 0.35, 0.15, 0.9] });
});

test("a selection dragged past an edge of the page stops at it", () => {
  const box = { x0: 0.1, y0: 0.1, x1: 0.3, y1: 0.2 };
  assert.deepEqual(clampShift(box, 0.2, 0.1, 0.75), { dx: 0.2, dy: 0.1 });
  assert.deepEqual(clampShift(box, -0.5, 0.9, 0.75), { dx: -0.1, dy: 0.55 });
  assert.deepEqual(clampShift(box, 0.9, -0.5, 0.75), { dx: 0.7, dy: -0.1 });
  // Already past the edge (a pen stroke's width): it can still move back in, not further out.
  const out = { x0: -0.01, y0: 0.1, x1: 0.2, y1: 0.2 };
  assert.deepEqual(clampShift(out, -0.05, 0, 0.75), { dx: 0, dy: 0 });
  assert.deepEqual(clampShift(out, 0.05, 0, 0.75), { dx: 0.05, dy: 0 });
});

import { scaleFactor, scaleStroke } from "../src/components/lessons/ink.ts";

test("a corner handle scales the selection around the opposite corner, by how far it is dragged along the diagonal", () => {
  const box = { x0: 0.2, y0: 0.2, x1: 0.4, y1: 0.3 };
  assert.equal(scaleFactor(box, "se", 0.4, 0.3, 0.75), 1);
  assert.ok(Math.abs(scaleFactor(box, "se", 0.6, 0.4, 0.75) - 2) < 1e-9);
  assert.ok(Math.abs(scaleFactor(box, "nw", 0.3, 0.25, 0.75) - 0.5) < 1e-9);
  // Off the diagonal the drag counts for its part along it: the strokes keep their proportions.
  assert.ok(Math.abs(scaleFactor(box, "se", 0.6, 0.3, 0.75) - scaleFactor(box, "se", 0.56, 0.38, 0.75)) < 1e-9);
});

test("a scaled selection stays on the page and never vanishes", () => {
  const box = { x0: 0.6, y0: 0.1, x1: 0.8, y1: 0.2 };
  assert.ok(Math.abs(scaleFactor(box, "se", 2, 2, 0.75) - 2) < 1e-9, "the right edge stops it at twice the size");
  assert.ok(scaleFactor(box, "se", 0.6, 0.1, 0.75) > 0);
  assert.ok(Math.abs(scaleFactor(box, "se", 0, 0, 0.75) - 0.05) < 1e-9, "its longer side at least 0.01");
  // Already past an edge: it can still grow towards the others.
  const out = { x0: -0.01, y0: 0.1, x1: 0.2, y1: 0.2 };
  assert.ok(scaleFactor(out, "se", 0.41, 0.3, 0.75) > 1.9);
});

test("a scaled stroke is a new one with its points and width scaled, the pressure untouched", () => {
  const s = { t: "pen", c: "#d32f2f", w: 0.003, p: [0.2, 0.2, 0.4, 0.3, 0.25, 0.9] };
  const m = scaleStroke(s, 0.2, 0.2, 2);
  assert.notEqual(m, s);
  assert.deepEqual(s.p, [0.2, 0.2, 0.4, 0.3, 0.25, 0.9]);
  assert.deepEqual(m, { t: "pen", c: "#d32f2f", w: 0.006, p: [0.2, 0.2, 0.4, 0.4, 0.3, 0.9] });
  assert.equal(scaleStroke({ ...s, w: 0.15 }, 0, 0, 3).w, 0.2, "no wider than the server takes");
});
