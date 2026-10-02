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
