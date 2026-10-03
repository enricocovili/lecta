// The draft of the whole document: every chapter typeset by LaTeX, block by block (paragraphs, headings, environments
// as SVG pictures), in one continuous scroll. A click picks a block (Shift+click extends the pick) and opens the
// toolbar to ask the AI about it; blocks are flashed where the AI just worked.
import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Icon } from "../icons";
import { pickBlock, pickScope, type BlockPick, type DraftSelection } from "./selection";
import SelectionToolbar, { type SelectionAction } from "./SelectionToolbar";
import type { DraftBlock, DraftChapter, FlashTarget } from "./types";

export interface FlashRequest {
  id: number;
  targets: FlashTarget[];
  scroll: boolean;
}

export interface ScrollRequest {
  nonce: number;
  chapterId: number;
  sectionId?: string;
}

const Block = memo(function Block({ b, index, width, picked }: { b: DraftBlock; index: number; width: number; picked: boolean }) {
  const failed = !!b.error;
  return (
    <div
      className={`doc-block${b.heading ? " doc-heading" : ""}${picked ? " picked" : ""}${failed ? " failed" : ""}`}
      data-line={b.start}
      data-end={b.end}
      data-index={index}
      id={b.id}
      tabIndex={b.pages.length || failed ? 0 : -1}
    >
      {b.pages.map((p) => (
        <img
          key={p.url}
          src={p.url}
          alt={(b.src ?? "").slice(0, 400)}
          className={p.raster ? "raster" : undefined}
          draggable={false}
          loading="lazy"
          decoding="async"
          style={{ width: `${(p.w / width) * 100}%`, marginLeft: `${(p.x / width) * 100}%`, aspectRatio: `${p.w} / ${p.h}` }}
        />
      ))}
      {failed && (
        <div className="doc-block-error">
          <span className="small">
            <Icon name="alert-triangle" /> Errore LaTeX, {b.error}
          </span>
          {!b.pages.length && <pre>{b.src}</pre>}
        </div>
      )}
    </div>
  );
});

/** Nothing written yet: at most the chapter title. */
const isHollow = (c: DraftChapter) => !!c.blocks && c.blocks.every((b) => b.heading === "chapter" || (!b.pages.length && !b.error));

/** Blocks of a chapter that cover some of the lines [from, to]. */
function blocksCovering(section: HTMLElement, from: number, to: number): HTMLElement[] {
  return Array.from(section.querySelectorAll<HTMLElement>(".doc-block")).filter((el) => Number(el.dataset.line) <= to && Number(el.dataset.end) >= from);
}

/** The viewport rectangle around the picked blocks. */
function pickRect(root: HTMLElement, pick: BlockPick): DraftSelection["rect"] | null {
  const sec = root.querySelector<HTMLElement>(`[data-chapter-id="${pick.chapterId}"]`);
  const a = sec?.querySelector<HTMLElement>(`.doc-block[data-index="${pick.from}"]`);
  const b = sec?.querySelector<HTMLElement>(`.doc-block[data-index="${pick.to}"]`);
  if (!a || !b) return null;
  const ra = a.getBoundingClientRect();
  const rb = b.getBoundingClientRect();
  return { top: ra.top, bottom: rb.bottom, left: Math.min(ra.left, rb.left), right: Math.max(ra.right, rb.right) };
}

export default function DocView({
  chapters,
  loading,
  error,
  flash,
  scrollTo,
  empty,
  onActiveChapter,
  onSelectionAction,
}: {
  chapters: DraftChapter[] | null;
  loading: boolean;
  error: string | null;
  flash: FlashRequest | null;
  scrollTo: ScrollRequest | null;
  empty: ReactNode;
  onActiveChapter: (id: number) => void;
  onSelectionAction: (a: SelectionAction, sel: DraftSelection) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const article = useRef<HTMLElement>(null);
  const [pick, setPick] = useState<BlockPick | null>(null);
  const [rect, setRect] = useState<DraftSelection["rect"] | null>(null);
  const activeRef = useRef<number | null>(null);

  // -- which chapter is being read
  const spy = useCallback(() => {
    const sc = scroller.current;
    const art = article.current;
    if (!sc || !art) return;
    const top = sc.getBoundingClientRect().top + 96;
    let current: number | null = null;
    for (const s of Array.from(art.querySelectorAll<HTMLElement>("[data-chapter-id]"))) {
      if (s.getBoundingClientRect().top <= top) current = Number(s.dataset.chapterId);
      else break;
    }
    const all = art.querySelectorAll<HTMLElement>("[data-chapter-id]");
    // at the very end the last chapter counts as being read, even if it is too short to reach the top
    if (all.length && sc.scrollTop > 0 && sc.scrollHeight - sc.scrollTop - sc.clientHeight < 6) current = Number(all[all.length - 1].dataset.chapterId);
    if (current === null && all.length) current = Number(all[0].dataset.chapterId);
    if (current !== null && current !== activeRef.current) {
      activeRef.current = current;
      onActiveChapter(current);
    }
  }, [onActiveChapter]);

  // -- keep the toolbar on the picked blocks while reading on
  const place = useCallback(() => {
    setRect(pick && article.current ? pickRect(article.current, pick) : null);
  }, [pick]);
  useEffect(place, [place, chapters]);

  useEffect(() => {
    const sc = scroller.current;
    if (!sc) return;
    let raf = 0;
    const on = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        spy();
        place();
      });
    };
    sc.addEventListener("scroll", on, { passive: true });
    window.addEventListener("resize", on);
    return () => {
      sc.removeEventListener("scroll", on);
      window.removeEventListener("resize", on);
      cancelAnimationFrame(raf);
    };
  }, [spy, place]);
  useEffect(spy, [chapters, spy]);

  // A pick whose blocks are gone (the chapter changed under it) is dropped.
  useEffect(() => {
    if (!pick) return;
    const ch = chapters?.find((c) => c.chapter.id === pick.chapterId);
    if (!ch?.blocks || pick.to >= ch.blocks.length) setPick(null);
  }, [chapters, pick]);

  // -- scroll to a chapter / section (outline, deep link, change card)
  useEffect(() => {
    if (!scrollTo || !article.current) return;
    const sec = article.current.querySelector<HTMLElement>(`[data-chapter-id="${scrollTo.chapterId}"]`);
    if (!sec) return;
    const target = (scrollTo.sectionId && sec.querySelector<HTMLElement>(`[id="${CSS.escape(scrollTo.sectionId)}"]`)) || sec;
    target.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [scrollTo]);

  // -- flash what the AI changed
  useEffect(() => {
    if (!flash || !article.current) return;
    const marked: HTMLElement[] = [];
    for (const t of flash.targets) {
      const sec = article.current.querySelector<HTMLElement>(`[data-chapter-id="${t.chapterId}"]`);
      if (sec) marked.push(...blocksCovering(sec, t.from, t.to));
    }
    marked.forEach((el) => el.classList.add("doc-flash"));
    if (flash.scroll && marked[0]) marked[0].scrollIntoView({ block: "center", behavior: "smooth" });
    const t = window.setTimeout(() => marked.forEach((el) => el.classList.remove("doc-flash")), 3200);
    return () => {
      window.clearTimeout(t);
      marked.forEach((el) => el.classList.remove("doc-flash"));
    };
  }, [flash]);

  // -- picking blocks
  const pickAt = (target: EventTarget | null, extend: boolean) => {
    const el = target instanceof Element ? target.closest<HTMLElement>(".doc-block") : null;
    const sec = el?.closest<HTMLElement>("[data-chapter-id]");
    if (!el || !sec || el.tabIndex < 0) {
      setPick(null);
      return;
    }
    setPick((cur) => pickBlock(cur, Number(sec.dataset.chapterId), Number(el.dataset.index), extend));
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPick(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const sel: DraftSelection | null = useMemo(() => {
    if (!pick || !rect) return null;
    const blocks = chapters?.find((c) => c.chapter.id === pick.chapterId)?.blocks;
    const scope = blocks && pickScope(blocks, pick);
    return scope ? { chapterId: pick.chapterId, selection: scope, rect } : null;
  }, [pick, rect, chapters]);

  const act = (a: SelectionAction) => {
    if (!sel) return;
    setPick(null);
    onSelectionAction(a, sel);
  };

  return (
    <div className="doc-scroll" ref={scroller}>
      <article
        className="doc"
        ref={article}
        data-testid="doc-preview"
        aria-label="Bozza del documento"
        aria-busy={loading}
        onClick={(e) => pickAt(e.target, e.shiftKey)}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && e.target instanceof HTMLElement && e.target.classList.contains("doc-block")) {
            e.preventDefault();
            pickAt(e.target, e.shiftKey);
          }
        }}
      >
        {error && !chapters && (
          <div className="alert danger">
            <Icon name="alert-circle" />
            <span>{error}</span>
          </div>
        )}
        {chapters === null && !error && (
          <div className="doc-loading muted" role="status">
            <Icon name="loader" className="spin" />
            Preparo la bozza…
          </div>
        )}
        {chapters && (chapters.length === 0 || chapters.every((c) => isHollow(c))) && empty}
        {chapters?.map((c, i) => (
          <section key={c.chapter.id} className="doc-ch" data-chapter-id={c.chapter.id} aria-label={c.chapter.title}>
            {c.blocks?.[0]?.heading !== "chapter" ? (
              <header className="doc-ch-head">
                <span className="doc-ch-n">Capitolo {i + 1}</span>
                <h1 className="doc-ch-title">{c.chapter.title}</h1>
              </header>
            ) : (
              <h1 className="sr-only">{c.chapter.title}</h1> // LaTeX draws the title: this one is for screen readers
            )}
            {c.error && (
              <div className="alert danger">
                <Icon name="alert-circle" />
                <span>{c.error}</span>
              </div>
            )}
            {c.blocks === null ? (
              <div className="doc-loading muted" role="status">
                <Icon name="loader" className="spin" />
                Composizione LaTeX…
              </div>
            ) : (
              c.blocks.map((b, k) => (
                <Block key={`${b.start}:${b.pages[0]?.url ?? b.error ?? ""}`} b={b} index={k} width={c.width} picked={!!pick && pick.chapterId === c.chapter.id && k >= pick.from && k <= pick.to} />
              ))
            )}
            {isHollow(c) && <p className="doc-ch-empty muted">Questo capitolo è ancora vuoto. Chiedi all’assistente di scriverlo.</p>}
          </section>
        ))}
      </article>
      {sel && <SelectionToolbar sel={sel} onAction={act} />}
    </div>
  );
}
