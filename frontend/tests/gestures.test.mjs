// Finger gestures of the lesson editor (palm or finger, the glide after a flick, the pinch zoom): run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { clampZoom, glide, isPalm, PALM_AFTER_PEN_MS, pinchView, reachable, releaseSpeed, stepZoom, wheelZoom, ZOOM_MAX, ZOOM_MIN } from "../src/components/lessons/gestures.ts";

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

const close = (a, b) => Math.abs(a - b) < 1e-9;

test("a pinch keeps the point that was between the fingers under them, wherever they move, and scales with their distance", () => {
  const origin = { x: 40, y: -300 };
  const f0 = { x: 400, y: 300 };
  const f = { x: 430, y: 250 };
  const v = pinchView(f0, 100, f, 150, origin, 1);
  assert.ok(close(v.s, 1.5));
  // The transform is translate(tx, ty) scale(s) from the pages' corner: the point f0 is drawn at f.
  assert.ok(close(origin.x + v.tx + v.s * (f0.x - origin.x), f.x));
  assert.ok(close(origin.y + v.ty + v.s * (f0.y - origin.y), f.y));
  // Fingers that only move together scroll the pages, without zooming.
  const pan = pinchView(f0, 100, f, 100, origin, 1);
  assert.ok(close(pan.s, 1) && close(pan.tx, 30) && close(pan.ty, -50));
});

// A scroller with its content box 1000 px wide from x = 20 and 800 px tall from y = 100; pages as wide as it, 6000 px tall
// with 160 px of padding above and below.
const room = { left: 20, width: 1000, top: 100, bottom: 900, w: 1000, h: 6000, padTop: 160, padBottom: 160 };

test("in the middle of the lesson, zoomed in, the point under the fingers can stay where it is", () => {
  const p = { x: 500, y: 450 };
  assert.deepEqual(reachable(room, { x: 480, y: 2350 }, 1.5, p), p);
  assert.deepEqual(reachable(room, { x: 480, y: 2350 }, 0.8, { x: 520, y: 450 }).y, 450);
});

test("pages zoomed out narrower than the editor are centred: the point goes where centring puts it", () => {
  // 1000 px → 600 px wide, centred: their left edge at 20 + 200; the point 480 px in is then 288 px in.
  assert.ok(close(reachable(room, { x: 480, y: 2350 }, 0.6, { x: 500, y: 450 }).x, 220 + 288));
  // Zoomed in, the pages scroll sideways only as far as they are wider than the editor.
  assert.ok(close(reachable(room, { x: 10, y: 2350 }, 2, { x: 900, y: 450 }).x, 20 + 20));
});

test("at the top of the lesson, zooming out cannot bring the first page further down than the top", () => {
  // The point is 300 px into the pages (140 below their padding): zoomed by a half, 160 + 70 from their top, which is at
  // 100 at most (scrolled all the way up).
  assert.ok(close(reachable(room, { x: 480, y: 300 }, 0.5, { x: 500, y: 600 }).y, 100 + 230));
  // At the bottom, the last page cannot go further up than the bottom of the editor.
  assert.ok(close(reachable(room, { x: 480, y: 5900 }, 0.5, { x: 500, y: 200 }).y, 900 - 100));
  // Pages shorter than the editor stay at the top.
  assert.ok(close(reachable({ ...room, h: 700 }, { x: 480, y: 400 }, 0.5, { x: 500, y: 800 }).y, 100 + 160 + 120));
});

test("the zoom stays within its limits, also when pinched beyond them", () => {
  assert.ok(close(pinchView({ x: 0, y: 0 }, 100, { x: 0, y: 0 }, 1000, { x: 0, y: 0 }, 2).s * 2, ZOOM_MAX));
  assert.ok(close(pinchView({ x: 0, y: 0 }, 100, { x: 0, y: 0 }, 1, { x: 0, y: 0 }, 1).s, ZOOM_MIN));
  assert.equal(clampZoom(9), ZOOM_MAX);
  assert.equal(clampZoom(0.1), ZOOM_MIN);
});

test("the − / + buttons go to the next step from any zoom, also one left between two steps by a pinch", () => {
  assert.equal(stepZoom(1, 1), 1.25);
  assert.equal(stepZoom(1, -1), 0.85);
  assert.equal(stepZoom(1.37, 1), 1.5);
  assert.equal(stepZoom(1.37, -1), 1.25);
  assert.equal(stepZoom(ZOOM_MAX, 1), ZOOM_MAX);
  assert.equal(stepZoom(ZOOM_MIN, -1), ZOOM_MIN);
});

test("a wheel notch zooms by a sensible step, a touchpad's small steps by a little, and up zooms in", () => {
  const notch = wheelZoom(1, -100, 0);
  assert.ok(notch > 1.2 && notch < 1.35, `notch ${notch}`);
  assert.ok(close(wheelZoom(1, 100, 0) * notch, 1));
  const pad = wheelZoom(1, 3, 0);
  assert.ok(pad < 1 && pad > 0.96);
  assert.ok(close(wheelZoom(1, -3, 1), notch)); // a wheel counting lines
});
