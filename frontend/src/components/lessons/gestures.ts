// Finger gestures over the lesson's pages, handled in one place for the whole scroller (the browser's own panning is off
// there, `touch-action: none`, or the pen could not draw): a finger scrolls, and keeps gliding for a moment when it is let
// go; the hand tool scrolls with the pen and the mouse too. A palm resting while writing is told apart from a finger by
// what the pen is doing, not by how long ago it was seen: hovering does not block the finger, so switching is immediate.
import { useEffect, type RefObject } from "react";

/** A touch that lands this soon after the pen left the screen is the palm resting between two strokes. */
export const PALM_AFTER_PEN_MS = 300;
/** The pen landing this soon after a touch began means the touch was the palm: its scrolling is undone. */
export const PALM_BEFORE_PEN_MS = 1500;
/** A contact wider than this (CSS px, on screens that report it) is a palm, not a fingertip. */
export const PALM_SIZE = 40;
/** A finger moves this far before the page follows (a palm settling down does not scroll). */
export const SLOP = 8;
/** The glide after a flick slows down by this factor every 16 ms, and stops under MIN_SPEED (px/ms). */
const FRICTION = 0.95;
const MIN_SPEED = 0.02;
/** Only the last moments of a drag count for the speed of the glide. */
const SPEED_WINDOW_MS = 80;

export interface PenState {
  /** the pen is touching the screen */
  down: boolean;
  /** when it last left it (performance.now()) */
  upAt: number;
}

/** Whether a touch landing now is a palm: the pen is writing or has just lifted between strokes, or the contact is large. */
export function isPalm(pen: PenState, now: number, contact: { width: number; height: number }): boolean {
  return pen.down || now - pen.upAt < PALM_AFTER_PEN_MS || Math.max(contact.width || 0, contact.height || 0) > PALM_SIZE;
}

export interface Sample {
  t: number;
  x: number;
  y: number;
}

/** The speed (px/ms) of the finger when it was let go at `now`, from its last positions; 0 if it had stopped. */
export function releaseSpeed(samples: Sample[], now: number): { vx: number; vy: number } {
  const recent = samples.filter((s) => now - s.t <= SPEED_WINDOW_MS);
  if (recent.length < 2) return { vx: 0, vy: 0 };
  const a = recent[0];
  const b = recent[recent.length - 1];
  const dt = Math.max(b.t - a.t, 8);
  return { vx: (b.x - a.x) / dt, vy: (b.y - a.y) / dt };
}

/** One frame of the glide: how far the content moves (px, opposite to the scroll) and the speed left. */
export function glide(vx: number, vy: number, dt: number): { dx: number; dy: number; vx: number; vy: number; done: boolean } {
  const k = Math.pow(FRICTION, dt / 16);
  const nx = vx * k;
  const ny = vy * k;
  return { dx: vx * dt, dy: vy * dt, vx: nx, vy: ny, done: Math.hypot(nx, ny) < MIN_SPEED };
}

// What the pen is doing, seen by the whole page (also over the toolbar).
const pen: PenState = { down: false, upAt: -Infinity };
if (typeof window !== "undefined") {
  window.addEventListener("pointerdown", (e) => e.pointerType === "pen" && (pen.down = true), true);
  const up = (e: PointerEvent) => {
    if (e.pointerType !== "pen") return;
    pen.down = false;
    pen.upAt = performance.now();
  };
  window.addEventListener("pointerup", up, true);
  window.addEventListener("pointercancel", up, true);
}

export interface GestureOptions {
  /** the tool in use ("hand": every pointer scrolls over the slides) */
  tool: string;
  /** a finger on a slide writes instead of scrolling */
  fingerDraws: boolean;
}

interface Pointer {
  /** wait: not moved enough yet; pan: scrolls the page; ink: left to the drawing surface; palm: ignored to the end */
  mode: "wait" | "pan" | "ink" | "palm";
  x: number;
  y: number;
  at: number;
  /** where the page was when this pointer landed, to put it back if it turns out to be a palm */
  scroll0: { left: number; top: number };
  samples: Sample[];
}

/** Wire the gestures to the scroller; `opts` is read at every event (tool and settings change while it runs). */
export function useGestures(scroller: RefObject<HTMLElement | null>, opts: RefObject<GestureOptions>): void {
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ptrs = new Map<number, Pointer>();
    let raf = 0;

    const stopGlide = () => {
      cancelAnimationFrame(raf);
      raf = 0;
    };
    const startGlide = (vx: number, vy: number) => {
      let last = performance.now();
      const frame = (now: number) => {
        const g = glide(vx, vy, Math.min(now - last, 48));
        last = now;
        el.scrollBy(-g.dx, -g.dy);
        vx = g.vx;
        vy = g.vy;
        raf = g.done ? 0 : requestAnimationFrame(frame);
      };
      if (Math.hypot(vx, vy) >= MIN_SPEED * 4) raf = requestAnimationFrame(frame);
    };
    const panning = () => [...ptrs.values()].some((p) => p.mode === "pan" || p.mode === "wait");

    const down = (e: PointerEvent) => {
      stopGlide();
      const now = performance.now();
      const o = opts.current;
      const onInk = !!(e.target as Element | null)?.closest?.(".les-ink");
      if (e.pointerType === "pen") {
        // The pen landed: the touches already down were the palm; a scroll they just made is undone.
        for (const p of ptrs.values()) {
          if (p.mode === "palm" || p.mode === "ink") continue;
          if (p.mode === "pan" && now - p.at < PALM_BEFORE_PEN_MS) el.scrollTo(p.scroll0);
          p.mode = "palm";
        }
      }
      if (e.pointerType !== "touch" && !(o.tool === "hand" && onInk && e.button === 0)) return;
      let mode: Pointer["mode"] = "wait";
      if (e.pointerType === "touch" && isPalm(pen, now, e)) mode = "palm";
      else if (e.pointerType === "touch" && onInk && o.fingerDraws && o.tool !== "hand") mode = "ink";
      else if (panning()) mode = "palm"; // a second finger does not scroll twice as fast
      ptrs.set(e.pointerId, { mode, x: e.clientX, y: e.clientY, at: now, scroll0: { left: el.scrollLeft, top: el.scrollTop }, samples: [{ t: now, x: e.clientX, y: e.clientY }] });
      if (mode === "ink") return;
      // The drawing surface never sees a finger that scrolls or a palm.
      e.stopPropagation();
      if (e.pointerType !== "touch") {
        e.preventDefault();
        try {
          el.setPointerCapture(e.pointerId);
        } catch {
          /* a synthetic event: nothing to capture */
        }
      }
    };

    const move = (e: PointerEvent) => {
      const p = ptrs.get(e.pointerId);
      if (!p || p.mode === "ink") return;
      e.stopPropagation();
      if (p.mode === "palm") return;
      const now = performance.now();
      p.samples.push({ t: now, x: e.clientX, y: e.clientY });
      if (p.samples.length > 12) p.samples.shift();
      if (p.mode === "wait") {
        if (Math.hypot(e.clientX - p.x, e.clientY - p.y) < SLOP) return;
        // From here on the page follows the finger, without jumping by the distance it took to start.
        p.mode = "pan";
        p.x = e.clientX;
        p.y = e.clientY;
        return;
      }
      el.scrollBy(p.x - e.clientX, p.y - e.clientY);
      p.x = e.clientX;
      p.y = e.clientY;
    };

    const up = (e: PointerEvent) => {
      const p = ptrs.get(e.pointerId);
      if (!p) return;
      ptrs.delete(e.pointerId);
      if (p.mode === "ink") return;
      e.stopPropagation();
      if (p.mode === "pan" && e.type === "pointerup" && !ptrs.size) {
        const v = releaseSpeed(p.samples, performance.now());
        startGlide(v.vx, v.vy);
      }
    };

    el.addEventListener("pointerdown", down, true);
    el.addEventListener("pointermove", move, true);
    el.addEventListener("pointerup", up, true);
    el.addEventListener("pointercancel", up, true);
    el.addEventListener("wheel", stopGlide, { passive: true });
    return () => {
      stopGlide();
      el.removeEventListener("pointerdown", down, true);
      el.removeEventListener("pointermove", move, true);
      el.removeEventListener("pointerup", up, true);
      el.removeEventListener("pointercancel", up, true);
      el.removeEventListener("wheel", stopGlide);
    };
  }, [scroller, opts]);
}
