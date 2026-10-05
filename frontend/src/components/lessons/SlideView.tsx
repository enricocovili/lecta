// One page of the slides drawn from the PDF into a canvas, only while it is near the viewport.
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
    const task = pdfjs.getDocument({ url, withCredentials: true });
    task.promise
      .then((d) => (cancelled ? d.loadingTask.destroy() : setDoc(d)))
      .catch((e) => !cancelled && setError(String(e?.message ?? e)));
    return () => {
      cancelled = true;
      task.destroy().catch(() => undefined);
    };
  }, [url]);
  return { doc, error };
}

export default function SlideView({ doc, pageNo, width, height, active }: { doc: PdfDoc; pageNo: number; width: number; height: number; active: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const done = useRef("");
  const dpr = canvasScale(width, height);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    if (!active || width < 8) {
      c.width = c.height = 0; // free the pixels of pages far from the viewport
      done.current = "";
      return;
    }
    const key = `${Math.round(width)}@${dpr.toFixed(3)}`;
    if (done.current === key && c.width > 0) return;
    let cancelled = false;
    let task: { cancel: () => void; promise: Promise<unknown> } | null = null;
    (async () => {
      const page = await doc.getPage(pageNo);
      if (cancelled) return;
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: (width / base.width) * dpr });
      // Draw off-screen and swap, so resizing never flashes a blank page.
      const off = document.createElement("canvas");
      off.width = Math.floor(viewport.width);
      off.height = Math.floor(viewport.height);
      task = page.render({ canvas: off, viewport });
      await task.promise;
      if (cancelled) return;
      c.width = off.width;
      c.height = off.height;
      c.getContext("2d")?.drawImage(off, 0, 0);
      done.current = key;
    })().catch(() => undefined);
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, pageNo, width, active, dpr]);

  return <canvas ref={canvas} className="les-slide-canvas" aria-label={`Slide ${pageNo}`} />;
}
