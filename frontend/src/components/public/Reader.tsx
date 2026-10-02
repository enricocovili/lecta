// Public course reader: the published PDF as a document (continuous pages, zoom, index with
// page numbers) or as a presentation (one page at a time, keyboard, fullscreen).
// Deep links: #p=7 (page), #ch-<chapter-slug> (chapter start, the old public URL scheme).
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { type PointerEvent as ReactPointerEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../icons";
import { sizeIt, type PubChapter, type PubCourse } from "./format";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type Doc = Awaited<ReturnType<typeof pdfjs.getDocument>["promise"]>;
type Mode = "doc" | "slides";
type Size = { w: number; h: number };
type Section = { title: string; page: number | null };
type RenderTask = { cancel: () => void; promise: Promise<unknown> };

const ZOOMS = [0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3];
const MAX_PAGE_W = 680; // "100%": the page fits the stage width, up to this many CSS px
const DPR = () => Math.min(2, window.devicePixelRatio || 1);

interface Props {
  course: PubCourse & { chapters: PubChapter[] };
  initialMode?: Mode;
}

/* ------------------------------------------------------------------ helpers */

const norm = (s: string) =>
  s
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/^[\d.\s]+/, "")
    .replace(/[^a-z0-9]+/g, "");

type OutlineNode = Awaited<ReturnType<Doc["getOutline"]>>[number];

async function destPage(doc: Doc, dest: OutlineNode["dest"]): Promise<number | null> {
  try {
    const d = typeof dest === "string" ? await doc.getDestination(dest) : dest;
    if (!d || !d.length) return null;
    const ref = d[0];
    if (typeof ref === "number") return ref + 1;
    return (await doc.getPageIndex(ref)) + 1;
  } catch {
    return null;
  }
}

/** For every chapter, its page (from the publish split, else the outline) and its sub-sections. */
async function readOutline(doc: Doc, chapters: PubChapter[]): Promise<{ pages: (number | null)[]; sections: Section[][] }> {
  const pages = chapters.map((c) => c.page_start);
  const sections: Section[][] = chapters.map(() => []);
  const top = (await doc.getOutline().catch(() => null)) ?? [];
  if (!top.length) return { pages, sections };
  const nodes = await Promise.all(top.map(async (n) => ({ n, page: await destPage(doc, n.dest) })));
  const used = new Set<number>();
  chapters.forEach((ch, i) => {
    const want = norm(ch.title);
    let k = nodes.findIndex((x, j) => !used.has(j) && norm(x.n.title) === want);
    if (k < 0 && ch.page_start) k = nodes.findIndex((x, j) => !used.has(j) && x.page === ch.page_start);
    if (k < 0) return;
    used.add(k);
    pages[i] ??= nodes[k]!.page;
  });
  await Promise.all(
    chapters.map(async (ch, i) => {
      const k = nodes.findIndex((x) => norm(x.n.title) === norm(ch.title) || (ch.page_start && x.page === ch.page_start));
      const kids = k >= 0 ? (nodes[k]!.n.items as OutlineNode[]) : [];
      sections[i] = await Promise.all(
        kids.map(async (s, j) => ({
          title: /^\d/.test(s.title) ? s.title : `${ch.position}.${j + 1} ${s.title}`,
          page: await destPage(doc, s.dest),
        })),
      );
    }),
  );
  return { pages, sections };
}

function readHash(): { page?: number; chapter?: string } {
  const h = decodeURIComponent(location.hash.slice(1));
  const m = /^(?:p|page)=(\d+)$/.exec(h);
  if (m) return { page: Number(m[1]) };
  if (h.startsWith("ch-")) return { chapter: h.slice(3) };
  return {};
}

function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);
  return [ref, size] as const;
}

// Render generation per page element: a newer paint (or an unload) makes older in-flight ones no-ops.
const gens = new WeakMap<HTMLElement, number>();

function unload(div: HTMLElement) {
  gens.set(div, (gens.get(div) ?? 0) + 1);
  delete div.dataset.key;
  div.replaceChildren();
}

/** Render one page into a fresh canvas (+ selectable text layer) and swap it in when done. */
async function paint(doc: Doc, div: HTMLElement, n: number, scale: number, withText: boolean, tasks: Set<RenderTask>) {
  const key = `${n}@${scale.toFixed(4)}`;
  if (div.dataset.key === key) return;
  const gen = (gens.get(div) ?? 0) + 1;
  gens.set(div, gen);
  div.dataset.key = key;
  const stale = () => gens.get(div) !== gen;
  const page = await doc.getPage(n);
  if (stale()) return;
  const vp = page.getViewport({ scale });
  const dpr = DPR();
  const canvas = document.createElement("canvas");
  canvas.width = Math.floor(vp.width * dpr);
  canvas.height = Math.floor(vp.height * dpr);
  const task = page.render({ canvas, viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined });
  tasks.add(task);
  try {
    await task.promise;
  } catch {
    if (!stale()) delete div.dataset.key;
    return;
  } finally {
    tasks.delete(task);
  }
  if (stale()) return;
  const layers: HTMLElement[] = [canvas];
  if (withText) {
    const tl = document.createElement("div");
    tl.className = "textLayer";
    try {
      await new pdfjs.TextLayer({ textContentSource: page.streamTextContent(), container: tl, viewport: vp }).render();
      layers.push(tl);
    } catch {
      /* text selection is a nicety */
    }
    if (stale()) return;
  }
  div.style.setProperty("--total-scale-factor", String(scale));
  div.replaceChildren(...layers);
}

/* ------------------------------------------------------------------ component */

export default function Reader({ course, initialMode = "doc" }: Props) {
  const chapters = course.chapters;
  const [mode, setMode] = useState<Mode>(initialMode);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sizes, setSizes] = useState<Size[]>([]);
  const [current, setCurrent] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [indexOpen, setIndexOpen] = useState(false);
  const [outline, setOutline] = useState<{ pages: (number | null)[]; sections: Section[][] }>({
    pages: chapters.map((c) => c.page_start),
    sections: chapters.map(() => []),
  });
  const numPages = doc?.numPages ?? course.page_count ?? 0;

  // Load the document, page sizes and outline.
  useEffect(() => {
    let cancelled = false;
    const task = pdfjs.getDocument({ url: course.pdf_url });
    task.promise
      .then(async (d) => {
        if (cancelled) return;
        const s = await Promise.all(
          Array.from({ length: d.numPages }, async (_, i) => {
            const vp = (await d.getPage(i + 1)).getViewport({ scale: 1 });
            return { w: vp.width, h: vp.height };
          }),
        );
        if (cancelled) return;
        setSizes(s);
        setDoc(d);
        readOutline(d, chapters).then((o) => !cancelled && setOutline(o));
      })
      .catch((e) => !cancelled && setError(String(e?.message ?? e)));
    return () => {
      cancelled = true;
      task.destroy();
    };
  }, [course.pdf_url]);

  // Current chapter = the last one starting at or before the current page.
  const chapterIdx = useMemo(() => {
    let idx = -1;
    outline.pages.forEach((p, i) => {
      if (p && p <= current && (idx < 0 || (outline.pages[idx] ?? 0) <= p)) idx = i;
    });
    return idx;
  }, [outline, current]);
  const chapter = chapterIdx >= 0 ? chapters[chapterIdx] : undefined;

  /* ---------------- document mode */

  const [stageRef, stage] = useElementSize<HTMLDivElement>();
  const scroller = useRef<HTMLDivElement>(null);
  const pageEls = useRef<(HTMLDivElement | null)[]>([]);
  const anchor = useRef({ page: 1, frac: 0 });
  const pendingJump = useRef<number | null>(null);
  const phone = stage.w > 0 && stage.w < 640;
  const pad = phone ? 12 : 32;
  // Room for the page: stage minus padding (and a classic scrollbar on desktop).
  const room = Math.min(stage.w - 2 * pad - (phone ? 0 : 16), MAX_PAGE_W);
  const baseScale = sizes[0] ? room / sizes[0].w : 1;
  const scale = Math.max(0.1, baseScale * zoom);

  const scrollToPage = useCallback(
    (n: number, smooth = false) => {
      const el = scroller.current;
      const div = pageEls.current[n - 1];
      if (!el || !div) return;
      el.scrollTo({ top: div.offsetTop - (phone ? 8 : 14), behavior: smooth ? "smooth" : "auto" });
    },
    [phone],
  );

  const goTo = useCallback(
    (n: number, smooth = true) => {
      if (!numPages) {
        pendingJump.current = n;
        return;
      }
      const p = Math.max(1, Math.min(numPages, n));
      setCurrent(p);
      anchor.current = { page: p, frac: 0 };
      if (mode === "doc") scrollToPage(p, smooth);
      setIndexOpen(false);
    },
    [numPages, mode, scrollToPage],
  );

  // Initial position (and later hash changes) from the URL.
  const fromHash = useCallback(() => {
    const h = readHash();
    if (h.page) return h.page;
    if (h.chapter) {
      const i = chapters.findIndex((c) => c.slug === h.chapter);
      if (i >= 0) return outline.pages[i] ?? chapters[i]!.page_start ?? null;
    }
    return null;
  }, [chapters, outline]);

  const started = useRef(false);
  useEffect(() => {
    if (!doc || started.current || stage.w === 0) return;
    started.current = true;
    const p = pendingJump.current ?? fromHash();
    if (p) requestAnimationFrame(() => goTo(p, false));
  }, [doc, stage.w, fromHash, goTo]);
  useEffect(() => {
    const on = () => {
      const p = fromHash();
      if (p) goTo(p, false);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, [fromHash, goTo]);

  // Reflect page and mode in the URL (shareable, no history spam).
  useEffect(() => {
    if (!doc) return;
    const t = setTimeout(() => {
      const u = new URL(location.href);
      if (mode === "slides") u.searchParams.set("mode", "slides");
      else u.searchParams.delete("mode");
      u.hash = current > 1 ? `p=${current}` : "";
      history.replaceState(null, "", u);
    }, 250);
    return () => clearTimeout(t);
  }, [doc, current, mode]);

  // Track the page in view.
  const onScroll = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const y = el.scrollTop + el.clientHeight * 0.35;
    let p = 1;
    for (let i = 0; i < pageEls.current.length; i++) {
      const d = pageEls.current[i];
      if (d && d.offsetTop <= y) p = i + 1;
      else break;
    }
    const d = pageEls.current[p - 1];
    if (d) anchor.current = { page: p, frac: Math.max(0, (el.scrollTop - d.offsetTop) / d.offsetHeight) };
    setCurrent(p);
  }, []);

  // Keep the reading position when the scale changes (zoom, resize, mode switch).
  useLayoutEffect(() => {
    if (mode !== "doc" || !doc) return;
    const el = scroller.current;
    const d = pageEls.current[anchor.current.page - 1];
    if (el && d && started.current) el.scrollTop = d.offsetTop + anchor.current.frac * d.offsetHeight;
  }, [scale, mode, doc]);

  // Lazily render the pages near the viewport; drop the far ones to bound memory.
  useEffect(() => {
    const el = scroller.current;
    if (!doc || !el || mode !== "doc" || stage.w === 0) return;
    const tasks = new Set<RenderTask>();
    const near = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const div = e.target as HTMLElement;
          if (e.isIntersecting) void paint(doc, div, Number(div.dataset.page), scale, true, tasks);
        }
      },
      { root: el, rootMargin: "800px 0px" },
    );
    const far = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const div = e.target as HTMLElement;
          if (!e.isIntersecting && div.dataset.key) unload(div);
        }
      },
      { root: el, rootMargin: "4000px 0px" },
    );
    pageEls.current.forEach((d) => {
      if (d) {
        near.observe(d);
        far.observe(d);
      }
    });
    return () => {
      near.disconnect();
      far.disconnect();
      tasks.forEach((t) => t.cancel());
    };
  }, [doc, mode, scale, stage.w, sizes]);

  /* ---------------- presentation mode */

  const slideRef = useRef<HTMLDivElement>(null);
  const presenter = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const slideSize = sizes[current - 1] ?? sizes[0];
  const slideScale = slideSize
    ? Math.max(0.1, Math.min((stage.w - (phone ? 16 : 64)) / slideSize.w, (stage.h - (phone ? 88 : 112)) / slideSize.h))
    : 1;

  useEffect(() => {
    const div = slideRef.current;
    if (!doc || mode !== "slides" || !div || stage.w === 0) return;
    const tasks = new Set<RenderTask>();
    void paint(doc, div, current, slideScale, false, tasks);
    if (current < doc.numPages) void doc.getPage(current + 1); // warm up the next slide
    return () => tasks.forEach((t) => t.cancel());
  }, [doc, mode, current, slideScale, stage.w]);

  // Swipe left/right on touch screens.
  const swipe = useRef<{ x: number; y: number } | null>(null);
  const onPointerDown = (e: ReactPointerEvent) => {
    if (e.pointerType !== "mouse") swipe.current = { x: e.clientX, y: e.clientY };
  };
  const onPointerUp = (e: ReactPointerEvent) => {
    const s = swipe.current;
    swipe.current = null;
    if (!s) return;
    const dx = e.clientX - s.x;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(e.clientY - s.y)) {
      setCurrent((c) => Math.max(1, Math.min(numPages || 1, c + (dx < 0 ? 1 : -1))));
    }
  };

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void presenter.current?.requestFullscreen?.().catch(() => undefined);
  }, []);
  useEffect(() => {
    const on = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);

  useEffect(() => {
    if (mode !== "slides") return;
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      // Space on a focused button/link activates it instead.
      const space = e.key === " " && !(t && /^(A|BUTTON)$/.test(t.tagName));
      const next = ["ArrowRight", "ArrowDown", "PageDown"].includes(e.key) || (space && !e.shiftKey);
      const prev = ["ArrowLeft", "ArrowUp", "PageUp"].includes(e.key) || (space && e.shiftKey);
      if (next || prev) {
        e.preventDefault();
        setCurrent((c) => Math.max(1, Math.min(numPages || 1, c + (next ? 1 : -1))));
      } else if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        setCurrent(e.key === "Home" ? 1 : numPages || 1);
      } else if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        toggleFullscreen();
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [mode, numPages, toggleFullscreen]);

  const switchMode = (m: Mode) => {
    if (m === mode) return;
    anchor.current = { page: current, frac: 0 };
    if (document.fullscreenElement) void document.exitFullscreen();
    setMode(m);
  };
  const zoomStep = (dir: 1 | -1) =>
    setZoom(
      dir > 0
        ? (ZOOMS.find((z) => z > zoom + 1e-6) ?? ZOOMS[ZOOMS.length - 1]!)
        : ([...ZOOMS].reverse().find((z) => z < zoom - 1e-6) ?? ZOOMS[0]!),
    );

  // Close the phone index drawer with Esc.
  useEffect(() => {
    if (!indexOpen) return;
    const on = (e: KeyboardEvent) => e.key === "Escape" && setIndexOpen(false);
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [indexOpen]);

  /* ---------------- render */

  const chapterLabel = chapter ? `Capitolo ${chapter.position} · ${chapter.title}` : `${chapters.length} capitoli`;
  const counter = numPages ? (
    <span className="pub-counter">
      Pagina <b>{current}</b> di {numPages}
    </span>
  ) : null;

  return (
    <div className={`pub-reader mode-${mode}${indexOpen ? " index-open" : ""}`}>
      <header className="pub-rbar">
        <div className="pub-rbar-left">
          <a className="pub-back" href="/" aria-label="Torna alle materie">
            <Icon name="arrow-left" />
            <span className="hide-mobile">Materie</span>
          </a>
          <span className="pub-rbar-div hide-mobile" />
          <div className="pub-rtitle">
            <div className="pub-rname">{course.name}</div>
            <div className="pub-rchap">{chapterLabel}</div>
          </div>
        </div>
        <div className="seg pub-modes" role="tablist" aria-label="Modalità di lettura">
          <button type="button" role="tab" aria-selected={mode === "doc"} onClick={() => switchMode("doc")}>
            <Icon name="file" />
            Documento
          </button>
          <button type="button" role="tab" aria-selected={mode === "slides"} onClick={() => switchMode("slides")}>
            <Icon name="monitor" />
            Presentazione
          </button>
        </div>
        <div className="pub-rbar-right">
          <span className="hide-mobile">{counter}</span>
          {mode === "doc" && (
            <div className="pub-zoom hide-mobile" role="group" aria-label="Zoom">
              <button type="button" className="btn icon sm" onClick={() => zoomStep(-1)} disabled={zoom <= ZOOMS[0]!} aria-label="Riduci">
                <Icon name="minus" />
              </button>
              <button type="button" className="pub-zoom-val" onClick={() => setZoom(1)} title="Adatta alla larghezza">
                {Math.round(zoom * 100)}%
              </button>
              <button
                type="button"
                className="btn icon sm"
                onClick={() => zoomStep(1)}
                disabled={zoom >= ZOOMS[ZOOMS.length - 1]!}
                aria-label="Ingrandisci"
              >
                <Icon name="plus" />
              </button>
            </div>
          )}
          <button
            type="button"
            className="btn ghost icon show-mobile pub-index-btn"
            onClick={() => setIndexOpen((o) => !o)}
            aria-expanded={indexOpen}
            aria-label="Indice"
          >
            <Icon name="list" />
          </button>
          <button className="btn ghost icon" data-theme-toggle type="button" aria-label="Cambia tema" title="Tema chiaro / scuro">
            <Icon name="moon" className="i-moon" />
            <Icon name="sun" className="i-sun" />
          </button>
          {course.source_url && (
            <a className="btn pub-dl" href={course.source_url} download={`${course.slug}-latex.zip`} aria-label="Scarica sorgente LaTeX" title="Sorgente LaTeX (.zip)">
              <Icon name="code" />
              <span className="hide-mobile">Scarica sorgente LaTeX</span>
            </a>
          )}
          <a className="btn primary pub-dl" href={course.pdf_url} download={`${course.slug}.pdf`} aria-label="Scarica PDF">
            <Icon name="download" />
            <span className="hide-mobile">Scarica PDF</span>
          </a>
        </div>
      </header>

      <div className="pub-rbody">
        <aside className="pub-index" aria-label="Indice">
          <div className="pub-index-scroll">
            <div className="pub-side-label">Indice</div>
            <ol className="pub-toc">
              {chapters.map((ch, i) => {
                const page = outline.pages[i];
                const active = i === chapterIdx;
                return (
                  <li key={ch.slug} id={`ch-${ch.slug}`}>
                    <a
                      href={page ? `#p=${page}` : (ch.pdf_url ?? course.pdf_url)}
                      className={active ? "active" : ""}
                      aria-current={active ? "true" : undefined}
                      onClick={(e) => {
                        if (!page) return;
                        e.preventDefault();
                        goTo(page);
                      }}
                    >
                      <span className="n">{ch.position}</span>
                      <span className="t">{ch.title}</span>
                      <span className="p">{page ?? ""}</span>
                    </a>
                    {active && outline.sections[i]!.length > 0 && (
                      <ol className="pub-toc-sub">
                        {outline.sections[i]!.map((s, j) => (
                          <li key={j}>
                            <a
                              href={s.page ? `#p=${s.page}` : "#"}
                              onClick={(e) => {
                                e.preventDefault();
                                if (s.page) goTo(s.page);
                              }}
                            >
                              <span className="t">{s.title}</span>
                              <span className="p">{s.page ?? ""}</span>
                            </a>
                          </li>
                        ))}
                      </ol>
                    )}
                  </li>
                );
              })}
            </ol>
          </div>
          <div className="pub-downloads">
            <div className="pub-side-label">Download</div>
            <a href={course.pdf_url} download={`${course.slug}.pdf`}>
              <Icon name="download" />
              <span className="t">Corso completo</span>
              <span className="p">PDF · {sizeIt(course.pdf_size)}</span>
            </a>
            {course.source_url && (
              <a href={course.source_url} download={`${course.slug}-latex.zip`}>
                <Icon name="code" />
                <span className="t">Sorgente LaTeX</span>
                <span className="p">ZIP{course.source_size ? ` · ${sizeIt(course.source_size)}` : ""}</span>
              </a>
            )}
            {chapter?.pdf_url && (
              <a href={chapter.pdf_url} download={`${course.slug}-${chapter.slug}.pdf`}>
                <Icon name="download" />
                <span className="t">Solo capitolo {chapter.position}</span>
                <span className="p">PDF · {sizeIt(chapter.pdf_size)}</span>
              </a>
            )}
          </div>
        </aside>
        {indexOpen && <div className="pub-index-backdrop" onClick={() => setIndexOpen(false)} />}

        <main className="pub-stage" ref={stageRef}>
          {error ? (
            <div className="pub-stage-msg">
              <div className="alert danger">Non riesco ad aprire il PDF qui ({error}).</div>
              <a className="btn" href={course.pdf_url}>
                <Icon name="external" />
                Apri il PDF
              </a>
            </div>
          ) : mode === "doc" ? (
            <div className="pub-pages" ref={scroller} onScroll={onScroll} style={{ padding: `${phone ? 12 : 32}px ${pad}px` }}>
              {sizes.length === 0 ? (
                <div className="pub-page skeleton" style={{ width: Math.max(0, room), aspectRatio: "210 / 297" }} />
              ) : (
                sizes.map((s, i) => (
                  <div
                    key={i}
                    ref={(d) => {
                      pageEls.current[i] = d;
                    }}
                    className="pub-page"
                    data-page={i + 1}
                    style={{ width: s.w * scale, height: s.h * scale }}
                    aria-label={`Pagina ${i + 1}`}
                  />
                ))
              )}
            </div>
          ) : (
            <div className={`pub-present${fullscreen ? " is-fs" : ""}`} ref={presenter}>
              <div className="pub-slide-wrap" onPointerDown={onPointerDown} onPointerUp={onPointerUp}>
                {slideSize ? (
                  <div
                    ref={slideRef}
                    className="pub-page pub-slide"
                    style={{ width: slideSize.w * slideScale, height: slideSize.h * slideScale }}
                    aria-label={`Pagina ${current}`}
                  />
                ) : (
                  <div className="pub-page skeleton" style={{ width: "min(60vw, 900px)", aspectRatio: "210 / 297", maxHeight: "70vh" }} />
                )}
              </div>
              <div className="pub-slide-bar">
                <button type="button" className="btn icon" onClick={() => setCurrent((c) => Math.max(1, c - 1))} disabled={current <= 1} aria-label="Pagina precedente">
                  <Icon name="chevron-left" />
                </button>
                <span className="pub-slide-count">
                  {current} / {numPages || "…"}
                </span>
                <button
                  type="button"
                  className="btn icon"
                  onClick={() => setCurrent((c) => Math.min(numPages || 1, c + 1))}
                  disabled={current >= numPages}
                  aria-label="Pagina successiva"
                >
                  <Icon name="chevron-right" />
                </button>
                <span className="pub-slide-sep" />
                <button type="button" className="btn ghost icon" onClick={toggleFullscreen} aria-label={fullscreen ? "Esci da schermo intero" : "Schermo intero"} title="Schermo intero (F)">
                  <Icon name={fullscreen ? "x" : "maximize"} />
                </button>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
