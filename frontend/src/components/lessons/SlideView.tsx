// One page of the slides drawn from the PDF into a canvas, only while it is near the viewport. Under it a small picture of
// the slide, made once for every page, shows while the sharp one is drawn: a slide coming into view or zoomed is never blank.
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useRef, useState } from "react";
import { canvasScale } from "./ink";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export type PdfDoc = Awaited<ReturnType<typeof pdfjs.getDocument>["promise"]>;

/** Load the slides once for the whole editor. */
export function usePdfDoc(url: string | null): { doc: PdfDoc | null; error: string | null } {
  const [doc, setDoc] = useState<PdfDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    let loaded: PdfDoc | null = null;
    const task = pdfjs.getDocument({ url, withCredentials: true });
    task.promise
      .then((d) => {
        if (cancelled) return d.loadingTask.destroy();
        loaded = d;
        setDoc(d);
      })
      .catch((e) => !cancelled && setError(String(e?.message ?? e)));
    return () => {
      cancelled = true;
      if (loaded) forgetThumbs(loaded);
      task.destroy().catch(() => undefined);
    };
  }, [url]);
  return { doc, error };
}

// ------------------------------------------------------------------ small pictures

/** The width (device px) of the small pictures: blurred at most zooms, but there at once. */
const THUMB_WIDTH = 480;

interface Thumbs {
  urls: Map<number, string>;
  /** the pages that want one, with how far each is from the viewport (the nearest is made first) */
  wanted: Map<number, () => number>;
  listeners: Map<number, Set<(url: string) => void>>;
  busy: boolean;
}
const thumbStore = new WeakMap<PdfDoc, Thumbs>();
/** Sharp pages being drawn: the small pictures wait for them. */
let drawing = 0;

function thumbsOf(doc: PdfDoc): Thumbs {
  let t = thumbStore.get(doc);
  if (!t) thumbStore.set(doc, (t = { urls: new Map(), wanted: new Map(), listeners: new Map(), busy: false }));
  return t;
}

function forgetThumbs(doc: PdfDoc) {
  const t = thumbStore.get(doc);
  if (!t) return;
  t.urls.forEach((u) => URL.revokeObjectURL(u));
  thumbStore.delete(doc);
}

/** Keep a small copy of a picture of the page (a sharp one just drawn, or one made for this). */
function keepThumb(doc: PdfDoc, pageNo: number, from: HTMLCanvasElement): Promise<void> {
  const t = thumbsOf(doc);
  if (t.urls.has(pageNo) || !from.width) return Promise.resolve();
  const small = document.createElement("canvas");
  small.width = Math.min(THUMB_WIDTH, from.width);
  small.height = Math.max(1, Math.round((from.height * small.width) / from.width));
  small.getContext("2d")?.drawImage(from, 0, 0, small.width, small.height);
  return new Promise((done) =>
    small.toBlob(
      (blob) => {
        if (blob && thumbStore.get(doc) === t && !t.urls.has(pageNo)) {
          const url = URL.createObjectURL(blob);
          t.urls.set(pageNo, url);
          t.wanted.delete(pageNo);
          t.listeners.get(pageNo)?.forEach((f) => f(url));
        }
        done();
      },
      "image/jpeg",
      0.85,
    ),
  );
}

const pause = (ms: number) => new Promise((ok) => setTimeout(ok, ms));

/** Make the wanted small pictures one at a time, the nearest to the viewport first, while no sharp page is being drawn. */
async function makeThumbs(doc: PdfDoc) {
  const t = thumbsOf(doc);
  if (t.busy) return;
  t.busy = true;
  try {
    while (t.wanted.size && thumbStore.get(doc) === t) {
      if (drawing) {
        await pause(150);
        continue;
      }
      let pageNo = 0;
      let best = Infinity;
      for (const [n, distance] of t.wanted) {
        const d = distance();
        if (d < best) [pageNo, best] = [n, d];
      }
      t.wanted.delete(pageNo);
      const page = await doc.getPage(pageNo);
      const base = page.getViewport({ scale: 1 });
      const off = document.createElement("canvas");
      const viewport = page.getViewport({ scale: THUMB_WIDTH / base.width });
      off.width = Math.floor(viewport.width);
      off.height = Math.floor(viewport.height);
      await page.render({ canvas: off, viewport }).promise;
      await keepThumb(doc, pageNo, off);
      await pause(0);
    }
  } catch {
    /* the document was closed */
  } finally {
    t.busy = false;
  }
}

/** The small picture of a page, once it is made (asked for as soon as the page is shown in the editor). */
function useThumb(doc: PdfDoc, pageNo: number, near: () => number): string | null {
  const [url, setUrl] = useState<string | null>(() => thumbsOf(doc).urls.get(pageNo) ?? null);
  const distance = useRef(near);
  distance.current = near;
  useEffect(() => {
    const t = thumbsOf(doc);
    const has = t.urls.get(pageNo);
    if (has) {
      setUrl(has);
      return;
    }
    const set = t.listeners.get(pageNo) ?? new Set();
    t.listeners.set(pageNo, set);
    set.add(setUrl);
    t.wanted.set(pageNo, () => distance.current());
    void makeThumbs(doc);
    return () => {
      set.delete(setUrl);
    };
  }, [doc, pageNo]);
  return url;
}

// ------------------------------------------------------------------ the page

/** After a zoom a page already drawn is drawn again at the new size only when the zoom has settled (zooming fast draws each
 *  page once), the ones in view first; until then the old picture stretches. */
const REDRAW_IN_VIEW_MS = 120;
const REDRAW_AWAY_MS = 450;

export default function SlideView({ doc, pageNo, width, height, active }: { doc: PdfDoc; pageNo: number; width: number; height: number; active: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const done = useRef("");
  const dpr = canvasScale(width, height);
  const thumb = useThumb(doc, pageNo, () => {
    const r = canvas.current?.getBoundingClientRect();
    return r ? Math.max(0, r.top - window.innerHeight, -r.bottom) : Infinity;
  });

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    if (!active || width < 8) {
      c.width = c.height = 0; // free the pixels of pages far from the viewport (the small picture stays)
      done.current = "";
      return;
    }
    const key = `${Math.round(width)}@${dpr.toFixed(3)}`;
    if (done.current === key && c.width > 0) return;
    let cancelled = false;
    let task: { cancel: () => void; promise: Promise<unknown> } | null = null;
    const draw = async () => {
      const page = await doc.getPage(pageNo);
      if (cancelled) return;
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: (width / base.width) * dpr });
      // Draw off-screen and swap, so resizing never flashes a blank page.
      const off = document.createElement("canvas");
      off.width = Math.floor(viewport.width);
      off.height = Math.floor(viewport.height);
      drawing++;
      try {
        task = page.render({ canvas: off, viewport });
        await task.promise;
      } finally {
        drawing--;
      }
      if (cancelled) return;
      c.width = off.width;
      c.height = off.height;
      c.getContext("2d")?.drawImage(off, 0, 0);
      done.current = key;
      void keepThumb(doc, pageNo, off);
    };
    let delay = 0;
    if (c.width > 0) {
      const r = c.getBoundingClientRect();
      delay = r.bottom > 0 && r.top < window.innerHeight ? REDRAW_IN_VIEW_MS : REDRAW_AWAY_MS;
    }
    const timer = window.setTimeout(() => draw().catch(() => undefined), delay);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      task?.cancel();
    };
  }, [doc, pageNo, width, active, dpr]);

  return (
    <>
      {thumb && <img className="les-slide-thumb" src={thumb} alt="" draggable={false} />}
      <canvas ref={canvas} className="les-slide-canvas" aria-label={`Slide ${pageNo}`} />
    </>
  );
}
