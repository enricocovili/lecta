import { useEffect, useState } from "react";
import { fmtSize, get } from "../lib/api";
import { Icon } from "./icons";
import { kindIcon } from "./pagekit";
import { Loading, Modal, useApi, usePoll, type Tone } from "./ui";
import { courseText } from "../lib/links";

export interface ItemView {
  id: number;
  key: string;
  source_file_id: number;
  page: number | null;
  kind: string;
  kind_source: string;
  label: string;
  title: string | null;
  text: string;
  has_latex: boolean;
  language: string | null;
  group_key: string | null;
  preview_url: string | null;
  image_url: string | null;
  meta?: { route?: string; why?: string[] };
}

/** A picture found in the sources and copied into the notes as an image. */
export interface FigureView {
  id: number;
  key: string;
  origin: string;
  status: string;
  path?: string | null;
  crop_url: string | null;
  item_id?: number | null;
}

/** Where an import wrote one group of material (job.result.groups). */
export interface ImportResult {
  type: "new_chapter" | "append" | null;
  title: string | null;
  group?: string | null;
  course_id: number | null;
  course_name: string | null;
  chapter_id: number | null;
  chapter_title: string | null;
  path: string | null;
  compile: string | null;
  /** The class notes the text follows (empty: a summary of the material alone). */
  notes?: string[];
}

interface Manifest {
  upload: { id: number; target_course_id: number | null; target_chapter_id: number | null; note: string | null } | null;
  files: { id: number; name: string; folder: string | null; kind: string; size: number; pages: number | null; status: string; reason: string | null; language: string | null }[];
  items: ItemView[];
  figures: FigureView[];
  results: ImportResult[];
}

/** Italian names of the item kinds the extraction assigns. */
export const ITEM_KIND: Record<string, string> = {
  page: "pagina",
  page_image: "letta con immagine",
  skipped: "saltata",
  handwritten: "a mano",
  photo: "foto",
  markdown: "appunti",
  text: "testo",
};

/** Why a page was read with its picture, or skipped. */
const WHY_IT: Record<string, string> = {
  scan: "scansione",
  "garbled text layer": "testo illeggibile",
  "handwritten annotations": "annotazioni a mano",
  "display math": "formule",
  "formulas stored as images": "formule come immagini",
  "text drawn as shapes": "testo vettorializzato",
  "overlay step repeated by the next page": "passaggio di un'animazione",
  "blank page": "pagina vuota",
};
const whyText = (it: ItemView) => (it.meta?.why ?? []).map((w) => WHY_IT[w] ?? w).join(", ");

export const FIG_ORIGIN: Record<string, string> = { image: "immagine", vector: "disegno vettoriale", drawing: "disegno dalla pagina", md_image: "immagine Markdown" };

/** Picture status → badge tone and label. */
export function figureStatus(status: string): { tone: Tone | "accent"; label: string } {
  switch (status) {
    case "used":
      return { tone: "ok", label: "inserita" };
    case "appended":
      return { tone: "warn", label: "aggiunta in fondo" };
    case "dropped":
      return { tone: "", label: "non usata" };
    default:
      return { tone: "", label: "in attesa" };
  }
}

/** One line per result: «Nuovo capitolo «X» in «Corso»» and a link to it. */
export function resultText(r: ImportResult): string {
  if (r.type === "new_chapter") return `Nuovo capitolo «${r.chapter_title ?? r.title}» in «${r.course_name}»`;
  if (r.type === "append") return `Aggiunto a «${r.chapter_title}»${r.course_name ? ` (${r.course_name})` : ""}`;
  return r.title ?? "";
}

export function resultHref(r: ImportResult): string | null {
  if (r.course_id) return courseText(r.course_id, { chapter: r.chapter_id });
  return null;
}

export function ItemCard({ it, onOpen }: { it: ItemView; onOpen: (it: ItemView) => void }) {
  return (
    <figure className={`pg-item ${it.kind === "skipped" ? "excluded" : ""}`} onClick={() => onOpen(it)} title={whyText(it) || undefined}>
      {it.preview_url ? (
        <img className="thumb" src={it.preview_url} alt={it.label} loading="lazy" />
      ) : (
        <pre className="thumb tiny pg-item-text">{it.text.slice(0, 300)}</pre>
      )}
      <figcaption>
        <span className={`badge ${it.kind === "page_image" ? "accent" : ""}`}>{ITEM_KIND[it.kind] ?? it.kind.replace("_", " ")}</span>{" "}
        {it.language && <span className="badge mono">{it.language}</span>}
        <div className="pg-item-label">{it.label}</div>
      </figcaption>
    </figure>
  );
}

export function ItemModal({ it, onClose }: { it: ItemView; onClose: () => void }) {
  return (
    <Modal title={it.label} onClose={onClose} wide>
      <div className="pg-split tight">
        <div>
          {it.image_url || it.preview_url ? <img src={it.image_url ?? it.preview_url!} alt="" className="pg-item-img" /> : <div className="muted small">Nessuna immagine</div>}
        </div>
        <div className="stack">
          <div className="small row" style={{ gap: ".4rem" }}>
            <span className="badge">{ITEM_KIND[it.kind] ?? it.kind}</span>
            <span className="muted">
              {it.kind === "page_image" ? `letta insieme alla sua immagine (${whyText(it)})` : it.kind === "skipped" ? whyText(it) : "testo estratto dal file"}
            </span>
          </div>
          {it.title && <strong>{it.title}</strong>}
          <pre className="small" style={{ maxHeight: "55vh" }}>{it.text || "(nessun testo)"}</pre>
          <a className="small" href={`/admin/sources/${it.source_file_id}`}>
            Apri l'originale <Icon name="arrow-right" />
          </a>
        </div>
      </div>
    </Modal>
  );
}

export function FigureCard({ f, page }: { f: FigureView; page?: string | null }) {
  const st = figureStatus(f.status);
  return (
    <figure className="pg-item pg-pic">
      {f.crop_url ? <img className="thumb" src={f.crop_url} alt={f.key} loading="lazy" /> : <div className="thumb" />}
      <figcaption>
        <span className={`badge ${st.tone}`}>{st.label}</span> <span className="tiny muted">{FIG_ORIGIN[f.origin] ?? f.origin}</span>
        <div className="pg-item-label">{f.path ? <span className="mono">{f.path}</span> : page ?? f.key}</div>
      </figcaption>
    </figure>
  );
}

export default function IngestManifest({ jobId, active }: { jobId: number; active: boolean }) {
  const { data, reload } = useApi(() => get<Manifest>(`/api/jobs/${jobId}/ingest`), [jobId]);
  usePoll(reload, 3000, active);
  // One last load when the job finishes (its results are written at the very end).
  useEffect(() => {
    if (!active) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
  const [open, setOpen] = useState<ItemView | null>(null);
  if (!data) return <Loading />;
  const groups = [...new Set(data.items.map((i) => i.group_key).filter(Boolean))] as string[];
  const titles = data.results.map((r) => r.group ?? r.title);
  const groupTitle = (g: string) => titles[Number(g.replace(/^g/, "")) - 1] ?? `Gruppo ${g}`;
  const itemLabel = new Map(data.items.map((i) => [i.id, i.label]));
  return (
    <div className="pg-manifest">
      {data.results.length > 0 && (
        <>
          <div className="section-label">Risultati</div>
          <div className="card flush">
            <div className="rows">
              {data.results.map((r, n) => {
                const href = resultHref(r);
                return (
                  <div key={n} className="pg-row">
                    <span className="status-ico pg-kind-ico accent-ico">
                      <Icon name="file-text" />
                    </span>
                    <div className="grow">
                      <div className="pg-row-title">{resultText(r)}</div>
                      <div className="pg-row-sub">
                        {r.notes && r.notes.length > 0 ? (
                          <>
                            dai tuoi appunti <span className="mono">{r.notes.join(", ")}</span>, completati con il materiale
                          </>
                        ) : (
                          "senza appunti: riassunto del materiale"
                        )}
                        {r.compile && (
                          <>
                            {" · "}
                            {r.compile === "ok" ? <span className="text-ok">compila</span> : <span className="text-danger">ha errori di compilazione: correggili dal testo della materia</span>}
                          </>
                        )}
                      </div>
                    </div>
                    {href && (
                      <a className="btn sm" href={href}>
                        Apri
                      </a>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
      <div className="section-label">
        File caricati <span className="pg-tab-n">{data.files.length}</span>
      </div>
      <div className="card flush">
        <div className="rows">
          {data.files.map((f) => (
            <div key={f.id} className="pg-row">
              <span className="status-ico pg-kind-ico">
                <Icon name={kindIcon(f.kind)} />
              </span>
              <div className="grow">
                <a className="pg-row-title" href={`/admin/sources/${f.id}`}>
                  {f.name}
                </a>
                <div className="pg-row-sub">
                  <span className="badge">{f.kind}</span>
                  {f.pages ? ` · ${f.pages} pagine` : ""}
                  {f.language ? ` · ${f.language}` : ""} · {fmtSize(f.size)}
                  {f.folder && <span className="mono"> · {f.folder}</span>}
                  {f.status !== "ok" && f.reason && <span> · {f.reason}</span>}
                </div>
              </div>
              {f.status === "ok" ? <span className="badge ok">letto</span> : <span className="badge warn">{f.status === "unsupported" ? "non supportato" : f.status === "skipped" ? "saltato" : f.status}</span>}
            </div>
          ))}
        </div>
      </div>
      {data.items.length > 0 && (
        <>
          <div className="section-label">
            Pagine ed elementi <span className="pg-tab-n">{data.items.length}</span>
          </div>
          <div className="card pg-pad stack">
            {groups.length > 0 ? (
              groups.map((g) => (
                <div key={g}>
                  <div className="pg-group-label">
                    <Icon name="layers" /> {groupTitle(g)}
                  </div>
                  <div className="thumbs">
                    {data.items
                      .filter((i) => i.group_key === g)
                      .map((it) => (
                        <ItemCard key={it.id} it={it} onOpen={setOpen} />
                      ))}
                  </div>
                </div>
              ))
            ) : (
              <div className="thumbs">
                {data.items.map((it) => (
                  <ItemCard key={it.id} it={it} onOpen={setOpen} />
                ))}
              </div>
            )}
          </div>
        </>
      )}
      {data.figures.length > 0 && (
        <>
          <div className="section-label">
            Immagini <span className="pg-tab-n">{data.figures.length}</span>
          </div>
          <div className="card pg-pad">
            <div className="thumbs">
              {data.figures.map((f) => (
                <FigureCard key={f.id} f={f} page={f.item_id ? itemLabel.get(f.item_id) : null} />
              ))}
            </div>
          </div>
        </>
      )}
      {open && <ItemModal it={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
