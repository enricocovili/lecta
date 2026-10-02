// The draft of the whole document: the preview HTML of every chapter in one continuous scroll,
// formulas rendered by KaTeX, blocks flashed where the AI just worked, and the selection toolbar.
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "../icons";
import { renderMath } from "./math";
import { readSelection, type DraftSelection } from "./selection";
import SelectionToolbar, { type SelectionAction } from "./SelectionToolbar";
import type { FlashTarget, PreviewChapter } from "./types";

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

const Body = memo(function Body({ html }: { html: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (ref.current) renderMath(ref.current);
  }, [html]);
  return <div ref={ref} className="doc-body" dangerouslySetInnerHTML={{ __html: html }} />;
});

const startsWithTitle = (html: string) => /^\s*<h1[\s>]/i.test(html);

/** Nothing written yet: at most the chapter title. */
const isHollow = (html: string) => !/<(img|figure|table|svg)\b/i.test(html) && !html.replace(/<h1[\s>][\s\S]*?<\/h1>/i, "").replace(/<[^>]*>/g, "").trim();

/** Blocks (outermost) of a chapter that cover some of the lines [from, to]. */
function blocksCovering(section: HTMLElement, from: number, to: number): HTMLElement[] {
  const all = Array.from(section.querySelectorAll<HTMLElement>("[data-line]"));
  const hit: HTMLElement[] = [];
  for (let i = 0; i < all.length; i++) {
    const el = all[i];
    const start = Number(el.dataset.line);
    let j = i + 1;
    while (j < all.length && el.contains(all[j])) j++;
    const end = j < all.length ? Number(all[j].dataset.line) - 1 : Infinity;
    if (start <= to && end >= from) hit.push(el);
  }
  return hit.filter((el) => !hit.some((o) => o !== el && o.contains(el)));
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
  chapters: PreviewChapter[] | null;
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
  const [sel, setSel] = useState<DraftSelection | null>(null);
  const pressedAt = useRef(0);
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

  useEffect(() => {
    const sc = scroller.current;
    if (!sc) return;
    let raf = 0;
    const on = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(spy);
    };
    sc.addEventListener("scroll", on, { passive: true });
    return () => {
      sc.removeEventListener("scroll", on);
      cancelAnimationFrame(raf);
    };
  }, [spy]);
  useEffect(spy, [chapters, spy]);

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

  // -- selection → toolbar
  useEffect(() => {
    let timer = 0;
    let pointerDown = false;
    const read = () => {
      const root = article.current;
      if (!root) return;
      const s = readSelection(root, window.getSelection());
      if (s) setSel(s);
      else if (Date.now() - pressedAt.current > 900) setSel(null);
    };
    const later = (ms: number) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(read, ms);
    };
    const inDoc = (t: EventTarget | null) => t instanceof Node && !!article.current?.contains(t);
    const onChange = () => {
      if (!pointerDown) later(280);
    };
    const onDown = (e: PointerEvent) => {
      if (inDoc(e.target)) pointerDown = true;
    };
    const onUp = () => {
      if (pointerDown) {
        pointerDown = false;
        later(30);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSel(null);
        window.getSelection()?.removeAllRanges();
      } else if (e.shiftKey || ((e.ctrlKey || e.metaKey) && e.key === "a")) later(60);
    };
    const onScroll = () => {
      // keep the toolbar on the selection while reading on
      const s = window.getSelection();
      if (!s || s.rangeCount === 0 || s.isCollapsed) return;
      const r = s.getRangeAt(0).getBoundingClientRect();
      setSel((cur) => (cur ? { ...cur, rect: { top: r.top, bottom: r.bottom, left: r.left, right: r.right } } : cur));
    };
    document.addEventListener("selectionchange", onChange);
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("pointerup", onUp, true);
    document.addEventListener("pointercancel", onUp, true);
    document.addEventListener("keyup", onKey);
    const sc = scroller.current;
    sc?.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("selectionchange", onChange);
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("pointerup", onUp, true);
      document.removeEventListener("pointercancel", onUp, true);
      document.removeEventListener("keyup", onKey);
      sc?.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  const act = (a: SelectionAction) => {
    if (!sel) return;
    const s = sel;
    setSel(null);
    if (a === "remove") window.getSelection()?.removeAllRanges();
    onSelectionAction(a, s);
  };

  return (
    <div className="doc-scroll" ref={scroller}>
      <article className="doc" ref={article} data-testid="doc-preview" aria-label="Bozza del documento" aria-busy={loading}>
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
        {chapters && (chapters.length === 0 || chapters.every((c) => isHollow(c.html))) && empty}
        {chapters?.map((c, i) => (
          <section key={c.chapter.id} className="doc-ch" data-chapter-id={c.chapter.id} aria-label={c.chapter.title}>
            {!startsWithTitle(c.html) && (
              <header className="doc-ch-head">
                <span className="doc-ch-n">Capitolo {i + 1}</span>
                <h1 className="doc-ch-title">{c.chapter.title}</h1>
              </header>
            )}
            {c.html.trim() && <Body html={c.html} />}
            {isHollow(c.html) && <p className="doc-ch-empty muted">Questo capitolo è ancora vuoto. Chiedi all’assistente di scriverlo.</p>}
          </section>
        ))}
      </article>
      {sel && <SelectionToolbar sel={sel} onAction={act} onPress={() => (pressedAt.current = Date.now())} />}
    </div>
  );
}
