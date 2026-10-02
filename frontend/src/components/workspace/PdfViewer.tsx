import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type Doc = Awaited<ReturnType<typeof pdfjs.getDocument>["promise"]>;

interface Props {
  url: string | null;
  /** Called with the page currently in view. */
  onPage?: (page: number) => void;
  emptyText?: string;
}

export default function PdfViewer({ url, onPage, emptyText = "Ancora nessun PDF: compila per vedere l’anteprima." }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState<number | "fit">("fit");
  const [width, setWidth] = useState(0);
  const [pageCount, setPageCount] = useState(0);
  const [current, setCurrent] = useState(1);
  const scrollRatio = useRef(0);
  const cbs = useRef({ onPage });
  cbs.current = { onPage };

  // Load (or reload) the document, remembering where we were.
  useEffect(() => {
    if (!url) {
      setDoc(null);
      return;
    }
    const el = scroller.current;
    if (el && el.scrollHeight > 0) scrollRatio.current = el.scrollTop / el.scrollHeight;
    let cancelled = false;
    const task = pdfjs.getDocument({ url, withCredentials: true, disableAutoFetch: false });
    task.promise
      .then((d) => {
        if (cancelled) {
          d.loadingTask.destroy();
          return;
        }
        setDoc((old) => {
          old?.loadingTask.destroy();
          return d;
        });
        setPageCount(d.numPages);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e?.message ?? e));
      });
    return () => {
      cancelled = true;
    };
  }, [url]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  // Render pages into canvases (lazily, as they scroll into view).
  useEffect(() => {
    const el = scroller.current;
    if (!doc || !el || width === 0) return;
    let cancelled = false;
    const container = el.querySelector(".pdf-pages") as HTMLDivElement;
    container.innerHTML = "";
    const renderTasks: { cancel: () => void }[] = [];
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const div = entry.target as HTMLDivElement & { rendered?: boolean };
          if (entry.isIntersecting && !div.rendered) {
            div.rendered = true;
            renderPage(div, Number(div.dataset.page));
          }
        }
      },
      { root: el, rootMargin: "600px 0px" },
    );
    const pageObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const p = Number((entry.target as HTMLElement).dataset.page);
            setCurrent(p);
            cbs.current.onPage?.(p);
          }
        }
      },
      { root: el, threshold: 0.5 },
    );

    const dpr = window.devicePixelRatio || 1;
    let scale = 1;
    const renderPage = async (div: HTMLDivElement, n: number) => {
      const page = await doc.getPage(n);
      if (cancelled) return;
      const viewport = page.getViewport({ scale });
      const canvas = document.createElement("canvas");
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      div.replaceChildren(canvas);
      const task = page.render({ canvas, viewport, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined });
      renderTasks.push(task);
      task.promise.catch(() => undefined);
    };

    (async () => {
      const first = await doc.getPage(1);
      if (cancelled) return;
      const base = first.getViewport({ scale: 1 });
      scale = zoom === "fit" ? Math.max(0.3, (width - 32) / base.width) : zoom;
      for (let n = 1; n <= doc.numPages; n++) {
        const div = document.createElement("div") as HTMLDivElement;
        div.className = "pdf-page";
        div.dataset.page = String(n);
        div.style.width = `${base.width * scale}px`;
        div.style.height = `${base.height * scale}px`;
        div.style.background = "#fff";
        container.appendChild(div);
        observer.observe(div);
        pageObserver.observe(div);
      }
      el.scrollTop = scrollRatio.current * el.scrollHeight;
    })();

    return () => {
      cancelled = true;
      observer.disconnect();
      pageObserver.disconnect();
      renderTasks.forEach((t) => t.cancel());
    };
  }, [doc, width, zoom]);

  const zoomBy = (f: number) => {
    const el = scroller.current;
    const w = el?.querySelector(".pdf-page") as HTMLElement | null;
    const currentScale = zoom === "fit" ? (w ? w.offsetWidth / 595 : 1) : zoom;
    setZoom(Math.max(0.3, Math.min(4, currentScale * f)));
  };

  return (
    <div className="pdfv">
      <div className="pane-head pdfv-head">
        <Icon name="file-text" />
        <span className="small muted mono">{pageCount ? `${current} / ${pageCount}` : "PDF"}</span>
        <span className="grow" />
        <button type="button" className="btn xs ghost icon" onClick={() => zoomBy(1 / 1.2)} aria-label="Riduci" title="Riduci">
          <Icon name="minus" />
        </button>
        <button type="button" className={`btn xs ${zoom === "fit" ? "active" : "ghost"}`} onClick={() => setZoom("fit")} title="Adatta alla larghezza">
          Adatta
        </button>
        <button type="button" className="btn xs ghost icon" onClick={() => zoomBy(1.2)} aria-label="Ingrandisci" title="Ingrandisci">
          <Icon name="plus" />
        </button>
        {url && (
          <a className="btn xs ghost icon" href={url} target="_blank" rel="noreferrer" aria-label="Apri il PDF" title="Apri il PDF">
            <Icon name="external" />
          </a>
        )}
      </div>
      <div className="pane-body pdf-view" ref={scroller}>
        {!url && <div className="pdfv-empty muted small">{emptyText}</div>}
        {error && <div className="alert danger small">{error}</div>}
        <div className="pdf-pages" />
      </div>
    </div>
  );
}
