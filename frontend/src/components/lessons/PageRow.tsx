// One page of the lesson: the slide with the drawing over it, and the Markdown notes next to it (or below).
import { memo, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { Icon } from "../icons";
import { Markdown } from "../workspace/Markdown";
import { bringIntoView, noteMarks, SlideMarks, useRenderedMarks, type PageFind } from "./FindBar";
import { zooming } from "./gestures";
import InkLayer from "./InkLayer";
import type { Rect, Stroke, Tool } from "./ink";
import NotesField from "./NotesField";
import SlideView, { type PdfDoc } from "./SlideView";
import type { PageState } from "./useLesson";

export interface DrawSettings {
  tool: Tool;
  color: string;
  hlColor: string;
  penWidth: number;
  hlWidth: number;
  /** font size of new text, in page widths */
  textSize: number;
  fingerDraws: boolean;
  shapes: boolean;
}

export interface Actions {
  setNotes: (pid: number, v: string) => void;
  addStroke: (pid: number, s: Stroke) => void;
  eraseStrokes: (pid: number, idx: number[]) => void;
  moveStrokes: (pid: number, moves: { from: Stroke; to: Stroke }[]) => void;
  /** the strokes of a page picked with the select tool (none: nothing selected) */
  select: (pid: number, strokes: Stroke[]) => void;
  addBlankAfter: (pid: number | null) => void;
  removePage: (pid: number) => void;
  visible: (index: number) => void;
  /** open the assistant on this page (the owner only) */
  ask?: (pid: number) => void;
  /** a rectangle drawn with the select tool on this page is an area to ask about (null: let go); the owner only */
  area?: (pid: number, r: Rect | null) => void;
}

interface Props {
  page: PageState;
  index: number;
  total: number;
  doc: PdfDoc | null;
  draw: DrawSettings;
  layout: "side" | "stack" | "slides";
  /** the zoom of the pages, to take the new size at once when it changes */
  zoom: number;
  preview: boolean;
  /** a read-only share link: nothing here changes the lesson */
  readOnly?: boolean;
  scroller: RefObject<HTMLElement | null>;
  actions: Actions;
  label: string;
  /** the strokes of this page that are selected (null: none here) */
  selected: Stroke[] | null;
  /** the area picked on this page to ask about (null: none here) */
  area?: Rect | null;
  /** what the search (Ctrl+F) found on this page (null: nothing here) */
  find?: PageFind | null;
}

function PageRow({ page, index, doc, draw, layout, zoom, preview, readOnly = false, scroller, actions, label, selected, area = null, find = null }: Props) {
  const row = useRef<HTMLElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const rendered = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [near, setNear] = useState(index < 3);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // A zoom or another layout is measured in the same commit (not a frame later, by the observer): the rows have their new
  // height before the editor puts the point under the fingers back, so nothing moves afterwards.
  useLayoutEffect(() => {
    if (box.current) setWidth(box.current.clientWidth);
  }, [zoom, layout]);

  // Near the viewport → the canvases exist; the band in the middle of the viewport → this is the current page.
  useEffect(() => {
    const el = row.current;
    const root = scroller.current;
    if (!el || !root) return;
    // While the pages are being zoomed they are only scaled on screen: what comes near is drawn once the zoom is applied.
    let seen: boolean | null = null;
    const io = new IntersectionObserver(
      (es) =>
        es.forEach((e) => {
          seen = e.isIntersecting;
          if (!zooming.on) setNear(seen);
        }),
      { root, rootMargin: "1400px 0px" },
    );
    const zoomed = () => seen !== null && setNear(seen);
    zooming.ended.add(zoomed);
    const mid = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && actions.visible(index)), { root, rootMargin: "-45% 0px -45% 0px" });
    io.observe(el);
    mid.observe(el);
    return () => {
      zooming.ended.delete(zoomed);
      io.disconnect();
      mid.disconnect();
    };
  }, [scroller, index, actions]);

  // The occurrence of the search being looked at is brought into view when the bar moves to it.
  const showing = layout !== "slides" && preview;
  const renderedHit = useRenderedMarks(rendered, page.id, find, showing);
  const seek = find?.current ? find.seek : 0;
  useEffect(() => {
    const root = scroller.current;
    if (!seek || !root) return;
    const frame = requestAnimationFrame(() => {
      const el = row.current?.querySelector("[data-find-current]");
      const r = el?.getBoundingClientRect() ?? renderedHit.current?.getBoundingClientRect() ?? row.current?.getBoundingClientRect();
      if (r) bringIntoView(root, r);
    });
    return () => cancelAnimationFrame(frame);
  }, [seek, scroller, renderedHit]);

  const height = width * page.ratio;
  const isSlide = page.kind === "slide" && doc && page.slide_page;
  return (
    <section ref={row} id={`lesson-page-${page.id}`} className={`les-row ${layout}`} data-testid="lesson-page" data-page-index={index}>
      <div className="les-slide" ref={box} style={{ aspectRatio: `${1 / page.ratio}` }}>
        {isSlide && <SlideView doc={doc} pageNo={page.slide_page!} width={width} height={height} active={near} />}
        {find && <SlideMarks find={find} ratio={page.ratio} />}
        <InkLayer
          strokes={page.ink}
          width={width}
          height={height}
          active={near}
          tool={draw.tool}
          color={draw.color}
          hlColor={draw.hlColor}
          penWidth={draw.penWidth}
          hlWidth={draw.hlWidth}
          textSize={draw.textSize}
          fingerDraws={draw.fingerDraws}
          shapes={draw.shapes}
          onAdd={(s) => actions.addStroke(page.id, s)}
          onErase={(idx) => actions.eraseStrokes(page.id, idx)}
          selected={selected}
          onSelect={(strokes) => actions.select(page.id, strokes)}
          onMoveStrokes={(moves) => actions.moveStrokes(page.id, moves)}
          area={area}
          onArea={actions.area && ((r) => actions.area!(page.id, r))}
          onAskArea={actions.ask && (() => actions.ask!(page.id))}
        />
        <span className="les-tag">{label}</span>
        {/* Without the notes column its buttons sit on the slide's corner. */}
        {layout === "slides" && actions.ask && (
          <button type="button" className="btn icon les-slide-ask" onClick={() => actions.ask!(page.id)} title="Chiedi all’assistente di questa pagina" aria-label="Chiedi all’assistente di questa pagina" data-testid="ask-page">
            <Icon name="sparkles" />
          </button>
        )}
        {layout === "slides" && !readOnly && (
          <button type="button" className="btn icon les-slide-remove" onClick={() => actions.removePage(page.id)} title={page.kind === "slide" ? "Togli questa slide dalla lezione" : "Togli questa pagina"} aria-label="Togli questa pagina" data-testid="remove-page">
            <Icon name="trash" />
          </button>
        )}
      </div>
      {layout !== "slides" && (
        <div className="les-notes">
          <div className="les-notes-head">
            <span className="les-notes-title">Appunti · {label}</span>
            {actions.ask && (
              <button type="button" className="btn ghost xs" onClick={() => actions.ask!(page.id)} title="Chiedi all’assistente di questa pagina: vede anche le slide vicine e legge tutta la lezione" data-testid="ask-page">
                <Icon name="sparkles" />
                Chiedi
              </button>
            )}
            {!readOnly && (
              <button type="button" className="btn ghost xs" onClick={() => actions.removePage(page.id)} title={page.kind === "slide" ? "Togli questa slide dalla lezione" : "Togli questa pagina"} aria-label="Togli questa pagina" data-testid="remove-page">
              <Icon name="trash" />
            </button>
            )}
          </div>
          {preview ? (
            <div className="les-notes-md" ref={rendered} style={{ minHeight: Math.max(120, height) }}>
              {page.notes.trim() ? <Markdown text={page.notes} /> : <span className="muted small">Nessun appunto.</span>}
            </div>
          ) : (
            <NotesField
              value={page.notes}
              onChange={(v) => actions.setNotes(page.id, v)}
              minHeight={Math.max(120, layout === "side" ? height : 140)}
              width={width}
              placeholder="Scrivi qui gli appunti in Markdown: elenchi con -, **grassetto**, formule con $…$"
              label={`Appunti di ${label}`}
              marks={noteMarks(find)}
            />
          )}
          {!readOnly && (
          <button type="button" className="btn ghost xs les-add" onClick={() => actions.addBlankAfter(page.id)} data-testid="add-blank-page">
            <Icon name="plus" />
            Pagina bianca dopo questa
          </button>
          )}
        </div>
      )}
    </section>
  );
}

export default memo(PageRow);
