import { useEffect, useState } from "react";
import { fmtSize, get } from "../lib/api";
import PdfViewer from "./workspace/PdfViewer";
import { Icon } from "./icons";
import { ItemCard, ItemModal, type ItemView } from "./IngestManifest";
import { fmtUpdated } from "./pagekit";
import { ErrorBox, Loading, useApi } from "./ui";

interface Source {
  id: number;
  upload_id: number;
  name: string;
  folder: string | null;
  kind: string;
  mime: string | null;
  size: number;
  pages: number | null;
  language: string | null;
  status: string;
  reason: string | null;
  created_at: string;
  has_original: boolean;
  items: ItemView[];
  links: { course_id: number; course_name: string; chapter_id: number | null; chapter_title: string | null; job_id: number | null }[];
}

export default function SourceView({ id }: { id: number }) {
  const { data, error, loading } = useApi(() => get<Source>(`/api/sources/${id}`), [id]);
  const [open, setOpen] = useState<ItemView | null>(null);
  const [text, setText] = useState<string | null>(null);
  const raw = `/api/sources/${id}/raw`;
  useEffect(() => {
    if (data && ["markdown", "text"].includes(data.kind)) {
      fetch(raw, { credentials: "same-origin" })
        .then((r) => r.text())
        .then(setText)
        .catch(() => setText(""));
    }
  }, [data, raw]);
  if (loading && !data) return <Loading />;
  if (error || !data) return <ErrorBox error={error ?? "Sorgente non trovata"} />;
  return (
    <div>
      <div className="page-head">
        <div className="grow" style={{ minWidth: 0 }}>
          <div className="row pg-head-pills">
            <span className="badge">{data.kind}</span>
            {data.status === "ok" ? (
              <span className="pill sm ok">
                <Icon name="check" />
                Letta
              </span>
            ) : (
              <span className="pill sm warn">
                <Icon name="alert-triangle" />
                {data.status}
              </span>
            )}
          </div>
          <h1 className="pg-h1-file">{data.name}</h1>
          <div className="sub">
            {data.folder && <span className="mono">{data.folder} · </span>}
            {fmtSize(data.size)}
            {data.pages ? ` · ${data.pages} pagine` : ""}
            {data.language ? ` · lingua ${data.language}` : ""} · caricata {fmtUpdated(data.created_at)}
          </div>
        </div>
        {data.has_original && (
          <a className="btn" href={`${raw}?download=1`}>
            <Icon name="download" />
            Scarica l'originale
          </a>
        )}
      </div>
      {data.status !== "ok" && (
        <div className="alert warn small" style={{ marginBottom: "1.25rem" }}>
          <Icon name="alert-triangle" />
          <span>
            {data.status}: {data.reason}
          </span>
        </div>
      )}
      {data.links.length > 0 && (
        <>
          <div className="section-label">Usata in</div>
          <div className="chips" style={{ marginBottom: "0.5rem" }}>
            {data.links.map((l, i) => (
              <a
                key={i}
                className="chip"
                href={l.chapter_id ? `/admin/courses/${l.course_id}?chapter=${l.chapter_id}` : `/admin/courses/${l.course_id}`}
              >
                <Icon name="folder" />
                {l.course_name}
                {l.chapter_title && <span className="muted">/ {l.chapter_title}</span>}
              </a>
            ))}
          </div>
        </>
      )}
      {(data.has_original && ["pdf", "image"].includes(data.kind)) || ["markdown", "text"].includes(data.kind) ? (
        <div className="section-label">Originale</div>
      ) : null}
      {data.kind === "pdf" && data.has_original && (
        <div className="card flush pg-source-pdf">
          <PdfViewer url={raw} />
        </div>
      )}
      {data.kind === "image" && data.has_original && (
        <div className="card pg-source-img">
          <img src={raw} alt={data.name} />
        </div>
      )}
      {["markdown", "text"].includes(data.kind) && <pre className="small pg-source-text">{text ?? "…"}</pre>}
      {data.items.length > 0 && (
        <>
          <div className="section-label">
            Come è stata analizzata <span className="pg-tab-n">{data.items.length}</span>
          </div>
          <div className="card pg-pad">
            <div className="thumbs">
              {data.items.map((it) => (
                <ItemCard key={it.id} it={it} onOpen={setOpen} />
              ))}
            </div>
          </div>
        </>
      )}
      {open && <ItemModal it={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
