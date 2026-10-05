// Finger gestures of the lesson editor (palm or finger, the glide after a flick): run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { glide, isPalm, PALM_AFTER_PEN_MS, releaseSpeed } from "../src/components/lessons/gestures.ts";

const finger = { width: 12, height: 14 };

test("a finger right after the pen left the screen scrolls; a touch while the pen writes, or just after a stroke, is a palm", () => {
  const away = { down: false, upAt: 1000 };
  assert.equal(isPalm(away, 1000 + PALM_AFTER_PEN_MS + 1, finger), false);
  assert.equal(isPalm(away, 1000 + 100, finger), true); // between two strokes
  assert.equal(isPalm({ down: true, upAt: 0 }, 50_000, finger), true);
  // The pen hovering over the screen does not matter: only touching it does.
  assert.equal(isPalm({ down: false, upAt: -Infinity }, 5, finger), false);
});

test("a wide contact is a palm, whatever the pen does; screens that report no size count as a fingertip", () => {
  const idle = { down: false, upAt: -Infinity };
  assert.equal(isPalm(idle, 0, { width: 70, height: 50 }), true);
  assert.equal(isPalm(idle, 0, { width: 1, height: 1 }), false);
  assert.equal(isPalm(idle, 0, { width: 0, height: 0 }), false);
});

test("the speed of a flick comes from the last moments of the drag, and is zero if the finger stopped before lifting", () => {
  const drag = [0, 1, 2, 3, 4, 5].map((i) => ({ t: 100 + i * 16, x: 0, y: i * 16 })); // 1 px/ms downwards
  const v = releaseSpeed(drag, 180);
  assert.ok(Math.abs(v.vy - 1) < 1e-9 && v.vx === 0);
  assert.deepEqual(releaseSpeed(drag, 600), { vx: 0, vy: 0 });
  assert.deepEqual(releaseSpeed([{ t: 0, x: 0, y: 0 }], 0), { vx: 0, vy: 0 });
});

test("the glide slows down at every frame and stops", () => {
  let vx = 0;
  let vy = 2;
  let moved = 0;
  let frames = 0;
  for (;;) {
    const g = glide(vx, vy, 16);
    moved += g.dy;
    assert.ok(Math.abs(g.vy) < Math.abs(vy));
    ({ vx, vy } = g);
    frames++;
    if (g.done) break;
    assert.ok(frames < 1000);
  }
  assert.ok(moved > 200 && moved < 1000, `glided ${moved}px`);
});
