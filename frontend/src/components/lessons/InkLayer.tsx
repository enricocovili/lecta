// The drawing surface over one page: pen, highlighter and stroke eraser, for mouse, pen and finger.
// A finger scrolls the page unless "finger draws" is on; a resting palm is ignored while a pen is around.
import { useCallback, useEffect, useRef, type RefObject } from "react";
import { drawAll, drawRegion, drawStroke, ERASER_RADIUS, farEnough, hits, roundStroke, strokeRect, unionRect, type Rect, type Stroke, type Tool } from "./ink";
import { recognize } from "./shapes";

// When a pen was last seen anywhere on the page: touches right after it are palms.
let lastPenAt = 0;
if (typeof window !== "undefined") {
  const seen = (e: PointerEvent) => {
    if (e.pointerType === "pen") lastPenAt = performance.now();
  };
  window.addEventListener("pointerdown", seen, true);
  window.addEventListener("pointermove", seen, true);
}
const PALM_MS = 1200;

interface Props {
  strokes: Stroke[];
  /** CSS size of the page in pixels. */
  width: number;
  height: number;
  /** Near the viewport: only then the canvases exist (a long deck would use too much memory otherwise). */
  active: boolean;
  tool: Tool;
  color: string;
  hlColor: string;
  penWidth: number;
  hlWidth: number;
  fingerDraws: boolean;
  /** A stroke that is a line, rectangle, triangle or ellipse is replaced by the clean shape (like Xournal++). */
  shapes: boolean;
  scroller: RefObject<HTMLElement | null>;
  onAdd: (s: Stroke) => void;
  onErase: (indices: number[]) => void;
}

type Current =
  | { kind: "pan"; id: number; x: number; y: number }
  | { kind: "draw"; id: number; stroke: Stroke }
  | { kind: "erase"; id: number; removed: Set<number>; dirty: Rect | null };

export default function InkLayer({ strokes, width, height, active, tool, color, hlColor, penWidth, hlWidth, fingerDraws, shapes, scroller, onAdd, onErase }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const base = useRef<HTMLCanvasElement>(null);
  const live = useRef<HTMLCanvasElement>(null);
  const cur = useRef<Current | null>(null);
  const raf = useRef(0);
  const rafErase = useRef(0);
  const justErased = useRef<{ gone: Set<Stroke>; len: number } | null>(null);
  const props = useRef({ strokes, tool, color, hlColor, penWidth, hlWidth, fingerDraws, shapes, width });
  props.current = { strokes, tool, color, hlColor, penWidth, hlWidth, fingerDraws, shapes, width };
  const dpr = typeof window === "undefined" ? 1 : Math.min(window.devicePixelRatio || 1, 2);

  const paintBase = useCallback(
    (skip?: Set<number>) => {
      const c = base.current;
      const ctx = c?.getContext("2d");
      if (!c || !ctx) return;
      ctx.clearRect(0, 0, c.width, c.height);
      drawAll(ctx, props.current.strokes, props.current.width * dpr, skip);
    },
    [dpr],
  );

  // (Re)size and repaint when the strokes or the size change; free the memory when far from the viewport.
  useEffect(() => {
    const b = base.current;
    const l = live.current;
    if (!b || !l) return;
    if (!active || width < 8) {
      b.width = b.height = l.width = l.height = 0;
      return;
    }
    const w = Math.round(width * dpr);
    const h = Math.round(height * dpr);
    const resized = b.width !== w || b.height !== h;
    if (resized) {
      b.width = l.width = w;
      b.height = l.height = h;
    }
    // The eraser has already redrawn what it took away: the strokes that came back from it need no second full repaint.
    const erased = justErased.current;
    justErased.current = null;
    if (!resized && erased && strokes.length === erased.len && !strokes.some((s) => erased.gone.has(s))) return;
    paintBase();
  }, [strokes, width, height, active, dpr, paintBase]);

  const paintLive = useCallback(() => {
    raf.current = 0;
    const l = live.current;
    const ctx = l?.getContext("2d");
    const c = cur.current;
    if (!l || !ctx) return;
    ctx.clearRect(0, 0, l.width, l.height);
    if (c?.kind === "draw") drawStroke(ctx, c.stroke, props.current.width * dpr);
  }, [dpr]);
  const schedule = () => {
    if (!raf.current) raf.current = requestAnimationFrame(paintLive);
  };
  useEffect(
    () => () => {
      cancelAnimationFrame(raf.current);
      cancelAnimationFrame(rafErase.current);
    },
    [],
  );

  const capture = (id: number) => {
    try {
      wrap.current?.setPointerCapture(id);
    } catch {
      /* the pointer is not active (a synthetic event): nothing to capture */
    }
  };

  const point = (e: { clientX: number; clientY: number }) => {
    const r = wrap.current!.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.width };
  };

  const eraseAt = (c: Extract<Current, { kind: "erase" }>, x: number, y: number) => {
    props.current.strokes.forEach((s, i) => {
      if (!c.removed.has(i) && hits(s, x, y, ERASER_RADIUS)) {
        c.removed.add(i);
        c.dirty = unionRect(c.dirty, strokeRect(s));
      }
    });
    // Redrawing is the costly part: once per frame however many points the eraser crossed, and only where something went.
    if (c.dirty && !rafErase.current) {
      rafErase.current = requestAnimationFrame(() => {
        rafErase.current = 0;
        const cur2 = cur.current;
        const ctx = base.current?.getContext("2d");
        if (cur2?.kind !== "erase" || !cur2.dirty || !ctx) return;
        const r = cur2.dirty;
        cur2.dirty = null;
        drawRegion(ctx, props.current.strokes, props.current.width * dpr, r, cur2.removed);
      });
    }
  };

  const cursorAt = (x: number | null, y: number | null) => {
    const l = live.current;
    const ctx = l?.getContext("2d");
    if (!l || !ctx || cur.current) return;
    ctx.clearRect(0, 0, l.width, l.height);
    if (x === null || y === null) return;
    const scale = props.current.width * dpr;
    ctx.beginPath();
    ctx.arc(x * scale, y * scale, ERASER_RADIUS * scale, 0, Math.PI * 2);
    ctx.lineWidth = Math.max(1, dpr);
    ctx.strokeStyle = "rgba(90,90,90,.8)";
    ctx.stroke();
  };

  const onDown = (e: React.PointerEvent) => {
    const p = props.current;
    if (p.tool === "hand" || cur.current) return;
    const type = e.pointerType;
    if (type === "mouse" && e.button !== 0) return;
    if (type === "touch" && performance.now() - lastPenAt < PALM_MS) return; // a palm
    if (type === "touch" && !p.fingerDraws) {
      cur.current = { kind: "pan", id: e.pointerId, x: e.clientX, y: e.clientY };
      capture(e.pointerId);
      return;
    }
    e.preventDefault();
    capture(e.pointerId);
    const pt = point(e);
    // The eraser end of a pen (button 32) erases whatever tool is chosen.
    const tool: Tool = e.buttons & 32 ? "eraser" : p.tool;
    if (tool === "eraser") {
      const c: Current = { kind: "erase", id: e.pointerId, removed: new Set(), dirty: null };
      cur.current = c;
      eraseAt(c, pt.x, pt.y);
      return;
    }
    const hl = tool === "hl";
    cur.current = { kind: "draw", id: e.pointerId, stroke: { t: hl ? "hl" : "pen", c: hl ? p.hlColor : p.color, w: hl ? p.hlWidth : p.penWidth, p: [pt.x, pt.y, e.pressure || 0.5] } };
    schedule();
  };

  const onMove = (e: React.PointerEvent) => {
    const c = cur.current;
    if (!c) {
      if (props.current.tool === "eraser" && e.pointerType !== "touch") {
        const pt = point(e);
        cursorAt(pt.x, pt.y);
      }
      return;
    }
    if (e.pointerId !== c.id) return;
    if (c.kind === "pan") {
      scroller.current?.scrollBy(c.x - e.clientX, c.y - e.clientY);
      c.x = e.clientX;
      c.y = e.clientY;
      return;
    }
    const events = (e.nativeEvent as PointerEvent).getCoalescedEvents?.() ?? [];
    for (const ev of events.length ? events : [e.nativeEvent as PointerEvent]) {
      const pt = point(ev);
      if (c.kind === "erase") eraseAt(c, pt.x, pt.y);
      else if (farEnough(c.stroke.p, pt.x, pt.y)) c.stroke.p.push(pt.x, pt.y, ev.pressure || 0.5);
    }
    if (c.kind === "draw") schedule();
  };

  const finish = (e: React.PointerEvent, cancelled: boolean) => {
    const c = cur.current;
    if (!c || e.pointerId !== c.id) return;
    cur.current = null;
    try {
      wrap.current?.releasePointerCapture(e.pointerId);
    } catch {
      /* already released, or never captured */
    }
    if (c.kind === "draw") {
      const l = live.current;
      l?.getContext("2d")?.clearRect(0, 0, l.width, l.height);
      if (!cancelled) {
        const done = roundStroke(c.stroke);
        onAdd((props.current.shapes && recognize(done)) || done);
      }
    } else if (c.kind === "erase") {
      cancelAnimationFrame(rafErase.current);
      rafErase.current = 0;
      if (cancelled) paintBase();
      else if (c.removed.size) {
        const ctx = base.current?.getContext("2d");
        if (ctx && c.dirty) drawRegion(ctx, props.current.strokes, props.current.width * dpr, c.dirty, c.removed);
        justErased.current = { gone: new Set([...c.removed].map((i) => props.current.strokes[i])), len: props.current.strokes.length - c.removed.size };
        onErase([...c.removed]);
      }
    }
  };

  const t = props.current.tool;
  return (
    <div
      ref={wrap}
      className={`les-ink tool-${t}`}
      style={{ touchAction: t === "hand" ? "pan-x pan-y pinch-zoom" : "none" }}
      data-testid="ink-surface"
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={(e) => finish(e, false)}
      onPointerCancel={(e) => finish(e, true)}
      onPointerLeave={() => cursorAt(null, null)}
      onContextMenu={(e) => e.preventDefault()}
    >
      <canvas ref={base} style={{ width, height }} />
      <canvas ref={live} style={{ width, height }} />
    </div>
  );
}
