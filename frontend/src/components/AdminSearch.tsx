import { useEffect, useRef, useState } from "react";
import { get } from "../lib/api";
import { Icon } from "./icons";
import { Empty, ErrorBox, Loading } from "./ui";

interface Result {
  files: { course_id: number; course_name: string; path: string; chapter_id: number | null; chapter_title: string | null; snippet: string; line: number | null }[];
  titles: { type: string; id: number; course_id?: number; title: string; course_name?: string }[];
  sources: { id: number; name: string; kind: string }[];
}

const SOURCE_KIND: Record<string, string> = { pdf: "PDF", image: "immagine", photo: "foto", markdown: "Markdown", zip: "zip", text: "testo", pptx: "PowerPoint", docx: "Word" };

/** The backend marks matches as <<term>>; fragments are joined with "...". */
function Snippet({ text }: { text: string }) {
  const flat = text.replace(/\s+/g, " ").trim();
  const parts = flat.split(/(<<.*?>>)/g);
  return (
    <code className="sch-snippet-text">
      {parts.map((p, i) => (p.startsWith("<<") ? <mark key={i}>{p.slice(2, -2)}</mark> : <span key={i}>{p}</span>))}
    </code>
  );
}

export default function AdminSearch() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Result | null>(null);
  const [done, setDone] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const run = async (query: string) => {
    const s = ++seq.current;
    if (!query.trim()) {
      setRes(null);
      setDone("");
      history.replaceState(null, "", location.pathname);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await get<Result>(`/api/search?q=${encodeURIComponent(query)}`);
      if (s !== seq.current) return;
      setRes(r);
      setDone(query);
      history.replaceState(null, "", `?q=${encodeURIComponent(query)}`);
    } catch (e) {
      if (s === seq.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (s === seq.current) setLoading(false);
    }
  };

  useEffect(() => {
    const initial = new URLSearchParams(location.search).get("q");
    if (initial) {
      setQ(initial);
      run(initial);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Live results while typing (debounced); Enter searches right away.
  const onType = (v: string) => {
    setQ(v);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => run(v), 350);
  };

  const total = res ? res.titles.length + res.files.length + res.sources.length : 0;

  return (
    <div className="sch-page">
      <div className="page-head">
        <h1>Cerca</h1>
      </div>
      <form
        className="sch-form"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          if (timer.current) clearTimeout(timer.current);
          run(q);
        }}
      >
        <Icon name="search" className="sch-form-ico" />
        <input
          type="search"
          aria-label="Cerca negli appunti"
          value={q}
          onChange={(e) => onType(e.target.value)}
          placeholder="Cerca negli appunti, nelle materie e nelle sorgenti"
          autoFocus
        />
        {loading ? <Icon name="loader" className="spin sch-form-busy" /> : null}
        <button className="btn primary" type="submit">
          Cerca
        </button>
      </form>
      {!res && (
      <p className="sch-hint">
        Ricerca full-text su tutti i sorgenti LaTeX, sui titoli di materie e capitoli e sui nomi dei file caricati. Prova per esempio{" "}
        <code>teorema di Lagrange</code>, <code>"risposta all'impulso"</code> o <code>\review</code>.
      </p>
      )}
      <ErrorBox error={error} />
      {loading && !res && <Loading text="Cerco…" />}
      {res && total === 0 && (
        <Empty icon="search">
          Nessun risultato per <strong>{done}</strong>.
        </Empty>
      )}
      {res && total > 0 && (
        <div className="sch-results">
          {res.titles.length > 0 && (
            <section>
              <div className="section-label">
                <span>
                  Materie e capitoli <span className="mono">· {res.titles.length}</span>
                </span>
              </div>
              <div className="card flush rows">
                {res.titles.map((t) => (
                  <a
                    key={`${t.type}${t.id}`}
                    className="sch-row"
                    href={t.type === "course" ? `/admin/courses/${t.id}` : `/admin/courses/${t.course_id}?chapter=${t.id}`}
                  >
                    <Icon name={t.type === "course" ? "folder" : "file-text"} />
                    <span className="grow sch-row-main">
                      <span className={t.type === "course" ? "sch-course" : "sch-title"}>{t.title}</span>
                      <span className="small muted">{t.type === "course" ? "Materia" : `Capitolo · ${t.course_name ?? ""}`}</span>
                    </span>
                    <Icon name="chevron-right" className="sch-chev" />
                  </a>
                ))}
              </div>
            </section>
          )}
          <section>
            <div className="section-label">
              <span>
                Nel testo <span className="mono">· {res.files.length}</span>
              </span>
            </div>
            {res.files.length === 0 ? (
              <div className="muted small">Nessuna corrispondenza nei sorgenti.</div>
            ) : (
              <div className="card flush rows">
                {res.files.map((f) => (
                  <a
                    key={`${f.course_id}:${f.path}`}
                    className="sch-hit"
                    href={`/admin/courses/${f.course_id}${f.chapter_id ? `?chapter=${f.chapter_id}` : ""}`}
                  >
                    <span className="sch-hit-head">
                      <span className="grow sch-row-main">
                        <span className="sch-title">{f.chapter_title ?? f.path}</span>
                        <span className="small muted sch-hit-meta">
                          <Icon name="folder" /> {f.course_name}
                          <span className="faint">·</span>
                          <span className="mono">
                            {f.path}
                            {f.line ? `:${f.line}` : ""}
                          </span>
                        </span>
                      </span>
                      <span className="sch-open">
                        Apri con l'AI <Icon name="arrow-right" />
                      </span>
                    </span>
                    {f.snippet && (
                      <span className="sch-snippet">
                        <span className="sch-ln mono" aria-label={f.line ? `riga ${f.line}` : undefined}>
                          {f.line ?? "…"}
                        </span>
                        <Snippet text={f.snippet} />
                      </span>
                    )}
                  </a>
                ))}
              </div>
            )}
          </section>
          {res.sources.length > 0 && (
            <section>
              <div className="section-label">
                <span>
                  Sorgenti <span className="mono">· {res.sources.length}</span>
                </span>
              </div>
              <div className="card flush rows">
                {res.sources.map((s) => (
                  <a key={s.id} className="sch-row" href={`/admin/sources/${s.id}`}>
                    <Icon name={s.kind === "image" || s.kind === "photo" ? "image" : "file"} />
                    <span className="grow sch-row-main">
                      <span className="mono sch-file">{s.name}</span>
                    </span>
                    <span className="badge">{SOURCE_KIND[s.kind] ?? s.kind}</span>
                    <Icon name="chevron-right" className="sch-chev" />
                  </a>
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
