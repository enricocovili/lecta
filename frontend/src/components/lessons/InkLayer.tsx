// The drawing surface over one page: pen, highlighter, stroke eraser and selection, for mouse, pen and finger.
// A finger only reaches it when "finger draws" is on: scrolling and palms are told apart before, by the gestures (gestures.ts).
// Selecting: a click on a stroke picks it (and dragging moves it at once), a drag on an empty spot draws a rectangle that picks
// what it encloses, a drag inside the selection's box moves it all; Shift adds to the selection. The selection itself is the
// editor's (one for the whole lesson), so that deleting it and the keyboard work from the toolbar; the button that deletes it
// is also attached to its box.
import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { fingerInk } from "./gestures";
import {
  boundsOf, canvasScale, clampShift, drawAll, drawRegion, drawStroke, ERASER_RADIUS, farEnough, hits, inside, moveStroke, pick, PICK_RADIUS, roundStroke, strokeRect, unionRect,
  type Rect, type Stroke, type Tool,
} from "./ink";
import { recognize } from "./shapes";

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
  /** the strokes of this page that are selected (null: none here) */
  selected: Stroke[] | null;
  onAdd: (s: Stroke) => void;
  onErase: (indices: number[]) => void;
  onSelect: (strokes: Stroke[]) => void;
  onMoveStrokes: (moves: { from: Stroke; to: Stroke }[]) => void;
}

/** Room around the selected strokes, in page widths: the box drawn, and where a press grabs the selection. */
const SEL_PAD = 0.006;
const SEL_COLOR = "#1e6fd8";

type Current =
  | { kind: "draw"; id: number; stroke: Stroke }
  | { kind: "erase"; id: number; removed: Set<number>; dirty: Rect | null }
  /** dragging the selection: the strokes leave the base canvas (once it really moves) and follow on the live one */
  | { kind: "move"; id: number; x0: number; y0: number; dx: number; dy: number; box: Rect; strokes: Stroke[]; idx: Set<number>; hidden: boolean }
  /** the selection rectangle being drawn; `add`: Shift, it adds to what is selected */
  | { kind: "rect"; id: number; x0: number; y0: number; x1: number; y1: number; add: boolean };

const pad = (r: Rect, m: number): Rect => ({ x0: r.x0 - m, y0: r.y0 - m, x1: r.x1 + m, y1: r.y1 + m });
const within = (r: Rect | null, x: number, y: number) => !!r && x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1;

/** The buttons attached to the selection's box (`r`, padded, in page widths), in CSS px over the page: the delete button
 *  above the box, or below it when there is no room above, or inside it when there is none below either. */
function SelectionControls({ r, width, height, onDelete }: { r: Rect; width: number; height: number; onDelete: () => void }) {
  const BTN = 36;
  const GAP = 8;
  const left = Math.min(Math.max(((r.x0 + r.x1) / 2) * width - BTN / 2, 4), width - BTN - 4);
  const above = r.y0 * width - BTN - GAP;
  const below = r.y1 * width + GAP;
  const top = above >= 4 ? above : below + BTN <= height - 4 ? below : Math.max(4, r.y0 * width + GAP);
  return (
    <div className="les-sel-ui" data-testid="selection-controls">
      <button
        type="button"
        className="btn icon les-sel-delete"
        style={{ left, top }}
        onPointerDown={(e) => e.stopPropagation()}
        onClick={onDelete}
        aria-label="Elimina la selezione"
        title="Elimina la selezione (Canc)"
        data-testid="selection-delete"
      >
        <Icon name="trash" />
      </button>
    </div>
  );
}

/** The selection's box (dashed, lightly filled), or the rectangle being drawn. */
function drawBox(ctx: CanvasRenderingContext2D, r: Rect, scale: number, dpr: number) {
  const x = Math.min(r.x0, r.x1) * scale;
  const y = Math.min(r.y0, r.y1) * scale;
  const w = Math.abs(r.x1 - r.x0) * scale;
  const h = Math.abs(r.y1 - r.y0) * scale;
  ctx.save();
  ctx.fillStyle = "rgba(30,111,216,.07)";
  ctx.fillRect(x, y, w, h);
  ctx.setLineDash([5 * dpr, 4 * dpr]);
  ctx.lineWidth = Math.max(1, dpr);
  ctx.strokeStyle = SEL_COLOR;
  ctx.strokeRect(x, y, w, h);
  ctx.restore();
}

export default function InkLayer({ strokes, width, height, active, tool, color, hlColor, penWidth, hlWidth, fingerDraws, shapes, selected, onAdd, onErase, onSelect, onMoveStrokes }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const base = useRef<HTMLCanvasElement>(null);
  const live = useRef<HTMLCanvasElement>(null);
  const cur = useRef<Current | null>(null);
  const raf = useRef(0);
  const rafErase = useRef(0);
  const justErased = useRef<{ gone: Set<Stroke>; len: number } | null>(null);
  // The selection is being moved, or a rectangle drawn: its buttons step aside.
  const [busy, setBusy] = useState(false);
  const props = useRef({ strokes, tool, color, hlColor, penWidth, hlWidth, fingerDraws, shapes, width, height, selected });
  props.current = { strokes, tool, color, hlColor, penWidth, hlWidth, fingerDraws, shapes, width, height, selected };
  const dpr = canvasScale(width, height);

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
    const scale = props.current.width * dpr;
    if (c?.kind === "draw") drawStroke(ctx, c.stroke, scale);
    else if (c?.kind === "move") {
      ctx.save();
      ctx.translate(c.dx * scale, c.dy * scale);
      drawAll(ctx, c.strokes, scale);
      drawBox(ctx, pad(c.box, SEL_PAD), scale, dpr);
      ctx.restore();
    } else if (c?.kind === "rect") drawBox(ctx, c, scale, dpr);
    if (c?.kind === "move") return;
    const box = boundsOf(props.current.selected ?? []);
    if (box) drawBox(ctx, pad(box, SEL_PAD), scale, dpr);
  }, [dpr]);
  const schedule = () => {
    if (!raf.current) raf.current = requestAnimationFrame(paintLive);
  };
  // The selection's box follows the selection, and comes back after a resize (which clears the canvas).
  useEffect(() => {
    if (active && !cur.current) schedule();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, strokes, width, height, active, dpr]);
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

  // The eraser's ring (the CSS cursor is hidden over the page): it follows the pointer while hovering and while erasing.
  const cursorAt = (x: number | null, y: number | null) => {
    const l = live.current;
    const ctx = l?.getContext("2d");
    if (!l || !ctx || (cur.current && cur.current.kind !== "erase")) return;
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
    if (type === "touch" && !p.fingerDraws) return;
    e.preventDefault();
    capture(e.pointerId);
    if (type === "touch") fingerInk.cancel = cancelFinger.current;
    const pt = point(e);
    // The eraser end of a pen (button 32) erases whatever tool is chosen.
    const tool: Tool = e.buttons & 32 ? "eraser" : p.tool;
    if (tool === "eraser") {
      const c: Current = { kind: "erase", id: e.pointerId, removed: new Set(), dirty: null };
      cur.current = c;
      eraseAt(c, pt.x, pt.y);
      cursorAt(pt.x, pt.y);
      return;
    }
    if (tool === "select") {
      const sel = p.selected ?? [];
      const add = e.shiftKey || e.ctrlKey || e.metaKey;
      const startMove = (picked: Stroke[]) => {
        const idx = new Set(picked.map((x) => p.strokes.indexOf(x)).filter((i) => i >= 0));
        cur.current = { kind: "move", id: e.pointerId, x0: pt.x, y0: pt.y, dx: 0, dy: 0, box: boundsOf(picked)!, strokes: picked, idx, hidden: false };
        setBusy(true);
      };
      const box = boundsOf(sel);
      if (!add && box && within(pad(box, SEL_PAD), pt.x, pt.y)) return startMove(sel);
      const i = pick(p.strokes, pt.x, pt.y, type === "mouse" ? PICK_RADIUS : PICK_RADIUS * 1.6);
      if (i >= 0) {
        const s = p.strokes[i];
        if (add) onSelect(sel.includes(s) ? sel.filter((x) => x !== s) : [...sel, s]);
        else {
          // Pressing on a stroke picks it, and a drag moves it straight away.
          onSelect([s]);
          startMove([s]);
        }
        return;
      }
      cur.current = { kind: "rect", id: e.pointerId, x0: pt.x, y0: pt.y, x1: pt.x, y1: pt.y, add };
      setBusy(true);
      schedule();
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
      } else if (props.current.tool === "select" && e.pointerType !== "touch" && wrap.current) {
        // Over the selection the pointer says it can be dragged.
        const pt = point(e);
        const box = boundsOf(props.current.selected ?? []);
        wrap.current.style.cursor = box && within(pad(box, SEL_PAD), pt.x, pt.y) ? "move" : "";
      }
      return;
    }
    if (e.pointerId !== c.id) return;
    if (c.kind === "move" || c.kind === "rect") {
      const pt = point(e);
      if (c.kind === "rect") {
        c.x1 = pt.x;
        c.y1 = pt.y;
      } else {
        const d = clampShift(c.box, pt.x - c.x0, pt.y - c.y0, props.current.height / props.current.width);
        c.dx = d.dx;
        c.dy = d.dy;
        if (!c.hidden) {
          c.hidden = true;
          paintBase(c.idx);
        }
      }
      schedule();
      return;
    }
    const events = (e.nativeEvent as PointerEvent).getCoalescedEvents?.() ?? [];
    let last = { x: 0, y: 0 };
    for (const ev of events.length ? events : [e.nativeEvent as PointerEvent]) {
      const pt = (last = point(ev));
      if (c.kind === "erase") eraseAt(c, pt.x, pt.y);
      else if (farEnough(c.stroke.p, pt.x, pt.y)) c.stroke.p.push(pt.x, pt.y, ev.pressure || 0.5);
    }
    if (c.kind === "draw") schedule();
    else cursorAt(last.x, last.y);
  };

  const finish = (id: number, cancelled: boolean) => {
    const c = cur.current;
    if (!c || id !== c.id) return;
    cur.current = null;
    setBusy(false);
    if (fingerInk.cancel === cancelFinger.current) fingerInk.cancel = null;
    try {
      wrap.current?.releasePointerCapture(id);
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
    } else if (c.kind === "move") {
      if (!cancelled && Math.hypot(c.dx, c.dy) > 0.0005) {
        onMoveStrokes(c.strokes.map((from) => ({ from, to: moveStroke(from, c.dx, c.dy) })));
      } else if (c.hidden) paintBase();
      schedule();
    } else if (c.kind === "rect") {
      if (!cancelled) {
        const sel = props.current.selected ?? [];
        const tiny = Math.abs(c.x1 - c.x0) < 0.004 && Math.abs(c.y1 - c.y0) < 0.004;
        const picked = tiny ? [] : props.current.strokes.filter((s) => inside(s, c));
        // A click on an empty spot lets go of the selection (with Shift it keeps it).
        if (c.add) onSelect([...sel, ...picked.filter((s) => !sel.includes(s))]);
        else onSelect(picked);
      }
      schedule();
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

  // A second finger landing turns the writing one into a pinch: what it was drawing or erasing is dropped.
  const finishRef = useRef(finish);
  finishRef.current = finish;
  const cancelFinger = useRef(() => {
    const c = cur.current;
    if (c) finishRef.current(c.id, true);
  });

  const t = props.current.tool;
  const selBox = !busy && active && selected?.length ? boundsOf(selected) : null;
  const deleteSelected = () => {
    const idx = (selected ?? []).map((s) => strokes.indexOf(s)).filter((i) => i >= 0);
    onSelect([]);
    if (idx.length) onErase(idx);
  };
  return (
    <div
      ref={wrap}
      className={`les-ink tool-${t}`}
      data-testid="ink-surface"
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={(e) => finish(e.pointerId, false)}
      onPointerCancel={(e) => finish(e.pointerId, true)}
      onPointerLeave={() => cursorAt(null, null)}
      onContextMenu={(e) => e.preventDefault()}
    >
      <canvas ref={base} />
      <canvas ref={live} />
      {selBox && <SelectionControls r={pad(selBox, SEL_PAD)} width={width} height={height} onDelete={deleteSelected} />}
    </div>
  );
}
