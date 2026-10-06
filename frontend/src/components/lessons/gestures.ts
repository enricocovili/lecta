// Finger gestures over the lesson's pages, handled in one place for the whole scroller (the browser's own panning is off
// there, `touch-action: none`, or the pen could not draw): a finger scrolls, and keeps gliding for a moment when it is let
// go; the hand tool scrolls with the pen and the mouse too. A palm resting while writing is told apart from a finger by
// what the pen is doing, not by how long ago it was seen: hovering does not block the finger, so switching is immediate.
// Two fingers zoom around the point between them (Ctrl + wheel, and a touchpad's pinch, too), like a drawing program: while
// the fingers move the pages are only scaled on screen (cheap), and when they are lifted the zoom is applied for real (the
// slides are drawn again, sharp) with the point under the fingers kept where it is.
import { useCallback, useEffect, useRef, type RefObject } from "react";
import { flushSync } from "react-dom";

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
/** After a zoom, its anchor is kept in place until the rows stop changing size for this long (and never longer than the other). */
const HOLD_QUIET_MS = 250;
const HOLD_MAX_MS = 1500;

/** How far the pages zoom out and in (1 = as wide as the editor), and the steps of the − / + buttons. */
export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 3;
export const ZOOM_STEPS = [0.5, 0.7, 0.85, 1, 1.25, 1.5, 2, 2.5, 3];

export function clampZoom(z: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
}

/** The next step of the buttons from any zoom (a pinch leaves it between two steps). */
export function stepZoom(z: number, dir: 1 | -1): number {
  const next = dir > 0 ? ZOOM_STEPS.find((s) => s > z + 0.01) : [...ZOOM_STEPS].reverse().find((s) => s < z - 0.01);
  return next ?? (dir > 0 ? ZOOM_MAX : ZOOM_MIN);
}

export interface Point {
  x: number;
  y: number;
}

/** How the pages are drawn while two fingers move: the point that was under the fingers (`f0`) follows them (`f`) and the
 *  scale follows their distance, within the zoom limits. `origin` is the pages' top-left corner on screen, the transform
 *  is `translate(tx, ty) scale(s)` from there. */
export function pinchView(f0: Point, d0: number, f: Point, d: number, origin: Point, z0: number): { s: number; tx: number; ty: number } {
  const s = clampZoom((z0 * d) / Math.max(d0, 1)) / z0;
  return { s, tx: f.x - origin.x - s * (f0.x - origin.x), ty: f.y - origin.y - s * (f0.y - origin.y) };
}

/** The scroller and the pages when a zoom begins (screen px), to know where the pages can be once it is applied. */
export interface Room {
  /** the scroller's content box, inside its padding: left edge and width */
  left: number;
  width: number;
  /** where the pages' top is when scrolled all the way up, and the lowest their bottom can be when scrolled all the way down */
  top: number;
  bottom: number;
  /** the pages' size before the zoom, and their padding above and below (it does not zoom) */
  w: number;
  h: number;
  padTop: number;
  padBottom: number;
}

/** Where the point `a` of the pages (px from their top-left corner, before the zoom) can be on screen once they are zoomed by
 *  `s`, as close as possible to `p`: the scroller cannot go past its ends, and pages narrower than it are centred. */
export function reachable(room: Room, a: Point, s: number, p: Point): Point {
  const w = room.w * s;
  const start = room.left + Math.max(0, (room.width - w) / 2); // the pages' left edge, scrolled all the way left
  const end = start - Math.max(0, w - room.width); // … and all the way right
  const x = Math.min(start + s * a.x, Math.max(end + s * a.x, p.x));
  // Rows zoom (nearly) with the pages, their padding does not: how far the point will be from their top and bottom.
  const zoomed = (d: number, pad: number) => (d <= pad ? d : pad + s * (d - pad));
  const lowest = room.top + zoomed(a.y, room.padTop);
  const highest = room.bottom - zoomed(room.h - a.y, room.padBottom);
  const y = highest >= lowest ? lowest : Math.min(lowest, Math.max(highest, p.y));
  return { x, y };
}

/** The zoom a wheel turn asks for: a touchpad pinch sends small steps, a mouse wheel notch a big one (kept to a fifth). */
export function wheelZoom(s: number, deltaY: number, deltaMode: number): number {
  const d = deltaMode === 1 ? deltaY * 16 : deltaY;
  return s * Math.exp(-Math.max(-25, Math.min(25, d)) * 0.01);
}

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
  zoom: number;
  setZoom: (z: number) => void;
}

/** The finger that is writing on a slide ("Dito scrive"), so that a second finger landing can turn the two into a pinch. */
export const fingerInk: { cancel: (() => void) | null } = { cancel: null };

interface Pointer {
  /** wait: not moved enough yet; pan: scrolls the page; ink: left to the drawing surface; pinch: one of the two fingers
   *  zooming; palm: ignored to the end */
  mode: "wait" | "pan" | "ink" | "pinch" | "palm";
  touch: boolean;
  /** where the page last followed it (pan) */
  x: number;
  y: number;
  /** where it is now */
  cx: number;
  cy: number;
  at: number;
  /** where the page was when this pointer landed, to put it back if it turns out to be a palm */
  scroll0: { left: number; top: number };
  samples: Sample[];
}

/** A zoom being made: the pages shown scaled until it ends. The anchor is a point of a slide, in units of its width. */
interface Zooming {
  pages: HTMLElement;
  origin: Point;
  room: Room;
  f0: Point;
  z0: number;
  s: number;
  f: Point;
  anchor: { el: Element; u: number; v: number } | null;
}

/** Wire the gestures to the scroller; `opts` is read at every event (tool and settings change while it runs). Returns how to
 *  zoom from elsewhere (the buttons), around the middle of the editor. */
export function useGestures(scroller: RefObject<HTMLElement | null>, opts: RefObject<GestureOptions>): (z: number) => void {
  const zoomRef = useRef<(z: number) => void>(() => undefined);
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ptrs = new Map<number, Pointer>();
    let raf = 0;
    let pinch: (Zooming & { ids: [number, number]; d0: number }) | null = null;
    let wheel: (Zooming & { timer: number }) | null = null;
    // How to stop keeping the last zoom's anchor in place (null: nothing is kept).
    let hold: (() => void) | null = null;
    const stopHold = () => {
      hold?.();
      hold = null;
    };

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

    // ---------------------------------------------------------------- zoom

    const beginZoom = (f0: Point): Zooming | null => {
      const pages = el.querySelector<HTMLElement>(".les-pages");
      if (!pages) return null;
      // The slide under the point (or the nearest one) keeps that point where it is when the zoom is applied.
      let anchor: Zooming["anchor"] = null;
      let best = Infinity;
      for (const s of pages.querySelectorAll(".les-slide")) {
        const r = s.getBoundingClientRect();
        const dist = Math.max(0, r.top - f0.y, f0.y - r.bottom);
        if (dist < best && r.width > 0) {
          best = dist;
          anchor = { el: s, u: (f0.x - r.left) / r.width, v: (f0.y - r.top) / r.width };
        }
        if (dist === 0) break;
      }
      const r = pages.getBoundingClientRect();
      const sr = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const ps = getComputedStyle(pages);
      const px = (v: string) => parseFloat(v) || 0;
      const room: Room = {
        left: sr.left + el.clientLeft + px(cs.paddingLeft),
        width: el.clientWidth - px(cs.paddingLeft) - px(cs.paddingRight),
        top: r.top + el.scrollTop,
        bottom: sr.top + el.clientTop + el.clientHeight - px(cs.paddingBottom),
        w: r.width,
        h: r.height,
        padTop: px(ps.paddingTop),
        padBottom: px(ps.paddingBottom),
      };
      pages.style.transformOrigin = "0 0";
      pages.style.willChange = "transform";
      return { pages, origin: { x: r.left, y: r.top }, room, f0, z0: opts.current.zoom, s: 1, f: f0, anchor };
    };
    /** Show the pages zoomed by `s`, the point that was at `f0` brought to `f`: or as close to it as the pages will be able to
     *  go once the zoom is applied (at the ends of the lesson, or centred when narrower than the editor), so that applying it
     *  moves nothing. */
    const showZoom = (z: Zooming, s: number, f: Point) => {
      const a = { x: z.f0.x - z.origin.x, y: z.f0.y - z.origin.y };
      z.s = s;
      z.f = reachable(z.room, a, s, f);
      z.pages.style.transform = `translate(${z.f.x - z.origin.x - s * a.x}px, ${z.f.y - z.origin.y - s * a.y}px) scale(${s})`;
    };
    const endZoom = (z: Zooming) => {
      stopHold();
      z.pages.style.transform = "";
      z.pages.style.willChange = "";
      const next = clampZoom(z.z0 * z.s);
      if (Math.abs(next - z.z0) > 0.001) flushSync(() => opts.current.setZoom(Math.round(next * 1000) / 1000));
      const anchor = z.anchor;
      if (!anchor?.el.isConnected) return;
      // The slides have their new size now: scroll so that the anchor is under the fingers again.
      const keep = () => {
        if (!anchor.el.isConnected) return;
        const r = anchor.el.getBoundingClientRect();
        const dx = r.left + anchor.u * r.width - z.f.x;
        const dy = r.top + anchor.v * r.width - z.f.y;
        if (Math.abs(dx) >= 1 || Math.abs(dy) >= 1) el.scrollBy(dx, dy);
      };
      keep();
      // The rest of the rows takes its new height a moment later (the notes follow the slide's width once it is measured,
      // their text wraps again): the rows above would push the anchor away, so it is put back at every change of size (after
      // layout, before the frame is drawn), until the rows settle or the user touches the pages.
      let quiet = window.setTimeout(stopHold, HOLD_QUIET_MS);
      const ro = new ResizeObserver(() => {
        keep();
        window.clearTimeout(quiet);
        quiet = window.setTimeout(stopHold, HOLD_QUIET_MS);
      });
      for (const row of z.pages.children) ro.observe(row);
      const cap = window.setTimeout(stopHold, HOLD_MAX_MS);
      hold = () => {
        ro.disconnect();
        window.clearTimeout(quiet);
        window.clearTimeout(cap);
      };
    };
    zoomRef.current = (target: number) => {
      const r = el.getBoundingClientRect();
      const c = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      const z = beginZoom(c);
      if (!z) return;
      z.s = clampZoom(target) / z.z0;
      z.f = reachable(z.room, { x: c.x - z.origin.x, y: c.y - z.origin.y }, z.s, c);
      endZoom(z);
    };

    const pinchMove = () => {
      if (!pinch) return;
      const [a, b] = pinch.ids.map((id) => ptrs.get(id)!);
      const f = { x: (a.cx + b.cx) / 2, y: (a.cy + b.cy) / 2 };
      showZoom(pinch, pinchView(pinch.f0, pinch.d0, f, Math.hypot(a.cx - b.cx, a.cy - b.cy), pinch.origin, pinch.z0).s, f);
    };

    const onWheel = (e: WheelEvent) => {
      stopGlide();
      stopHold();
      if (!e.ctrlKey) return; // a touchpad's pinch comes as Ctrl + wheel too
      e.preventDefault();
      if (pinch) return;
      if (!wheel) {
        const z = beginZoom({ x: e.clientX, y: e.clientY });
        if (!z) return;
        wheel = { ...z, timer: 0 };
      }
      const w = wheel;
      const s = clampZoom(w.z0 * wheelZoom(w.s, e.deltaY, e.deltaMode)) / w.z0;
      showZoom(w, s, w.f0);
      window.clearTimeout(w.timer);
      w.timer = window.setTimeout(() => {
        wheel = null;
        endZoom(w);
      }, 180);
    };

    // ---------------------------------------------------------------- pointers

    const down = (e: PointerEvent) => {
      stopGlide();
      stopHold();
      const now = performance.now();
      const o = opts.current;
      const onInk = !!(e.target as Element | null)?.closest?.(".les-ink");
      if (e.pointerType === "pen") {
        // The pen landed: the touches already down were the palm; a scroll they just made is undone.
        for (const p of ptrs.values()) {
          if (p.mode === "palm" || p.mode === "ink" || p.mode === "pinch") continue;
          if (p.mode === "pan" && now - p.at < PALM_BEFORE_PEN_MS) el.scrollTo(p.scroll0);
          p.mode = "palm";
        }
      }
      if (e.pointerType !== "touch" && !(o.tool === "hand" && onInk && e.button === 0)) return;
      const live = [...ptrs.entries()].filter(([, p]) => p.mode !== "palm");
      let mode: Pointer["mode"] = "wait";
      if (e.pointerType === "touch" && isPalm(pen, now, e)) mode = "palm";
      else if (e.pointerType === "touch" && live.length === 1 && live[0][1].touch && live[0][1].mode !== "pinch" && !wheel) {
        // A second finger: the two zoom (a stroke the first one was writing is dropped).
        const [id0, first] = live[0];
        if (first.mode === "ink") fingerInk.cancel?.();
        first.mode = "pinch";
        mode = "pinch";
        const f0 = { x: (first.cx + e.clientX) / 2, y: (first.cy + e.clientY) / 2 };
        const z = beginZoom(f0);
        if (z) pinch = { ...z, ids: [id0, e.pointerId], d0: Math.hypot(first.cx - e.clientX, first.cy - e.clientY) };
      } else if (live.length) mode = "palm"; // a third finger, or a finger while the pen scrolls
      else if (e.pointerType === "touch" && onInk && o.fingerDraws && o.tool !== "hand") mode = "ink";
      ptrs.set(e.pointerId, { mode, touch: e.pointerType === "touch", x: e.clientX, y: e.clientY, cx: e.clientX, cy: e.clientY, at: now, scroll0: { left: el.scrollLeft, top: el.scrollTop }, samples: [{ t: now, x: e.clientX, y: e.clientY }] });
      if (mode === "ink") return;
      // The drawing surface never sees a finger that scrolls or zooms, or a palm.
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
      if (!p) return;
      p.cx = e.clientX;
      p.cy = e.clientY;
      if (p.mode === "ink") return;
      e.stopPropagation();
      if (p.mode === "palm") return;
      if (p.mode === "pinch") {
        pinchMove();
        return;
      }
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
      if (p.mode === "pinch") {
        // One finger lifted ends the zoom; the other one stays still until it is lifted too (no jump into a scroll).
        const z = pinch;
        pinch = null;
        for (const q of ptrs.values()) if (q.mode === "pinch") q.mode = "palm";
        if (z) endZoom(z);
        return;
      }
      if (p.mode === "pan" && e.type === "pointerup" && !ptrs.size) {
        const v = releaseSpeed(p.samples, performance.now());
        startGlide(v.vx, v.vy);
      }
    };

    el.addEventListener("pointerdown", down, true);
    el.addEventListener("pointermove", move, true);
    el.addEventListener("pointerup", up, true);
    el.addEventListener("pointercancel", up, true);
    el.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("keydown", stopHold, true); // ← / → go to another page
    return () => {
      stopGlide();
      stopHold();
      window.removeEventListener("keydown", stopHold, true);
      if (wheel) window.clearTimeout(wheel.timer);
      el.removeEventListener("pointerdown", down, true);
      el.removeEventListener("pointermove", move, true);
      el.removeEventListener("pointerup", up, true);
      el.removeEventListener("pointercancel", up, true);
      el.removeEventListener("wheel", onWheel);
    };
  }, [scroller, opts]);
  return useCallback((z: number) => zoomRef.current(z), []);
}
