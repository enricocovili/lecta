// A PDF of the lab, page after page (each drawn only while near the viewport), with a «Commenta» on every page.
import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import SlideView, { usePdfDoc } from "../lessons/SlideView";

const MAX_WIDTH = 920;

export default function PdfPages({
  url,
  counts,
  activePage,
  canComment,
  onComment,
}: {
  url: string;
  counts: Map<number, number>;
  activePage: number | null;
  canComment: boolean;
  onComment: (page: number) => void;
}) {
  const { doc, error } = usePdfDoc(url);
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [ratios, setRatios] = useState<number[]>([]);
  const [near, setNear] = useState<Set<number>>(new Set([1, 2]));

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.min(MAX_WIDTH, el.clientWidth - 48)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!doc) return;
    let alive = true;
    void Promise.all(
      Array.from({ length: doc.numPages }, (_, i) => doc.getPage(i + 1).then((p) => {
        const v = p.getViewport({ scale: 1 });
        return v.height / v.width;
      })),
    ).then((r) => alive && setRatios(r));
    return () => {
      alive = false;
    };
  }, [doc]);

  // Only the pages near the viewport keep their pixels.
  useEffect(() => {
    const root = box.current;
    if (!root || !ratios.length) return;
    const io = new IntersectionObserver(
      (entries) =>
        setNear((cur) => {
          const next = new Set(cur);
          for (const e of entries) {
            const n = Number((e.target as HTMLElement).dataset.page);
            if (e.isIntersecting) next.add(n);
            else next.delete(n);
          }
          return next;
        }),
      { root, rootMargin: "800px 0px" },
    );
    root.querySelectorAll<HTMLElement>(".lab-pdf-page").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [ratios]);

  if (error) return <div className="alert danger">Non riesco a leggere il PDF: {error}</div>;
  return (
    <div className="lab-pdf" ref={box} data-testid="lab-pdf">
      {ratios.map((ratio, i) => {
        const n = i + 1;
        return (
          <section key={n} id={`lab-page-${n}`} data-page={n} className={`lab-pdf-page ${activePage === n ? "on" : ""}`} style={{ width }}>
            <div className="lab-pdf-head">
              <span className="muted small">Pagina {n}</span>
              <span className="grow" />
              {counts.get(n) ? <span className="lab-count" title={`${counts.get(n)} commenti`}>{counts.get(n)}</span> : null}
              {canComment && (
                <button type="button" className="btn ghost sm" onClick={() => onComment(n)} title="Commenta questa pagina">
                  <Icon name="message" /> Commenta
                </button>
              )}
            </div>
            <div className="lab-pdf-sheet" style={{ width, height: width * ratio }}>
              {doc && width > 0 && <SlideView doc={doc} pageNo={n} width={width} height={width * ratio} active={near.has(n)} />}
            </div>
          </section>
        );
      })}
    </div>
  );
}
