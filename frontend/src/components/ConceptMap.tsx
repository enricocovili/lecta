import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { get } from "../lib/api";
import { Icon } from "./icons";
import { computeLayout, type MapConcept, type MapCourse, type MapData, type Occurrence } from "./map/layout";
import { COURSE_TONE } from "./pagekit";
import { Dot, Empty, ErrorBox, Loading, useApi, usePoll } from "./ui";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** "§4 Ottimizzazione › 4.1 SGD e momentum" */
function occPath(o: Occurrence): string {
  const chapter = `§${o.chapter_number} ${o.chapter_title}`;
  if (o.level === "chapter") return chapter;
  return `${chapter} › ${o.section_number ? o.section_number + " " : ""}${o.section}`;
}

function pdfHref(c: MapCourse | undefined, id: number): string {
  return c?.published ? `/api/public/courses/${encodeURIComponent(c.slug)}.pdf` : `/api/courses/${id}/pdf`;
}

function OccurrenceCard({ o, course }: { o: Occurrence; course: MapCourse | undefined }) {
  return (
    <div className="map-occ">
      <div className="map-occ-course">
        <Dot tone={COURSE_TONE[course?.status ?? "ok"]} />
        {o.course_name}
      </div>
      <div className="map-occ-path">{occPath(o)}</div>
      <div className="map-occ-actions">
        <a className="btn sm" href={`/admin/courses/${o.course_id}${o.chapter_id ? `?chapter=${o.chapter_id}` : ""}`}>
          <Icon name="sparkles" />
          Apri
        </a>
        <a className="btn sm" href={pdfHref(course, o.course_id)} target="_blank" rel="noreferrer">
          <Icon name="download" />
          PDF
        </a>
      </div>
    </div>
  );
}

function Detail({ concept, courses, mode }: { concept: MapConcept | undefined; courses: Map<number, MapCourse>; mode: MapData["mode"] }) {
  if (!concept) return <div className="card map-detail muted">Seleziona un concetto nella mappa.</div>;
  return (
    <aside className="card map-detail" aria-live="polite">
      <div className="section-label">Concetto condiviso</div>
      <h2 className="map-detail-title">{concept.label}</h2>
      <div className="muted">Compare in {plural(concept.course_ids.length, "materia", "materie")}</div>
      <div className="map-occs">
        {concept.occurrences.map((o) => (
          <OccurrenceCard key={`${o.chapter_id}:${o.line}`} o={o} course={courses.get(o.course_id)} />
        ))}
      </div>
      <div className="map-mode">{mode === "semantic" ? "Abbinamenti per titolo e per contenuto (embeddings)" : "Abbinamenti per titolo delle sezioni"}</div>
    </aside>
  );
}

function Legend() {
  return (
    <div className="map-legend" aria-hidden="true">
      <span>
        <i className="map-lg-course" />
        Materia
      </span>
      <span>
        <i className="map-lg-concept" />
        Concetto condiviso
      </span>
      <span>
        <Dot tone="ok" />
        Completata
      </span>
      <span>
        <Dot tone="warn" />
        In lavorazione
      </span>
      <span>
        <Dot tone="danger" />
        Errore
      </span>
    </div>
  );
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)));
    ro.observe(el);
    setWidth(Math.round(el.getBoundingClientRect().width));
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

function Graph({
  data,
  selected,
  focusCourse,
  onConcept,
  onCourse,
}: {
  data: MapData;
  selected: string | null;
  focusCourse: number | null;
  onConcept: (id: string) => void;
  onCourse: (id: number) => void;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const lay = useMemo(() => (width > 0 ? computeLayout(data, width) : null), [data, width]);
  const sel = data.concepts.find((k) => k.id === selected);
  const focusSet = new Set(focusCourse !== null ? data.concepts.filter((k) => k.course_ids.includes(focusCourse)).map((k) => k.id) : []);
  const edgeClass = (course: number, concept: string) =>
    concept === selected ? "sel" : focusCourse === course ? "focus" : focusCourse !== null ? "dim" : "";
  // Selected / focused edges are drawn last so they sit on top.
  const edges = lay ? [...lay.edges].sort((a, b) => (edgeClass(a.course, a.concept) ? 1 : 0) - (edgeClass(b.course, b.concept) ? 1 : 0)) : [];

  return (
    <div className="card map-card">
      <div className="map-canvas" ref={ref} style={{ height: lay?.height ?? 560 }}>
        {lay && (
          <>
            <svg className="map-edges" width={width} height={lay.height} aria-hidden="true">
              {edges.map((e) => (
                <path key={`${e.course}-${e.concept}`} d={e.d} className={edgeClass(e.course, e.concept)} />
              ))}
            </svg>
            {data.courses.map((c) => {
              const b = lay.courses.get(c.id);
              if (!b) return null;
              const on = focusCourse === c.id || (sel?.course_ids.includes(c.id) ?? false);
              return (
                <button
                  key={c.id}
                  type="button"
                  className={`map-course ${on ? "on" : ""} ${focusCourse === c.id ? "focus" : ""}`}
                  style={{ left: b.x, top: b.y, width: b.w }}
                  aria-pressed={focusCourse === c.id}
                  onClick={() => onCourse(c.id)}
                >
                  <span className="map-course-name">{c.name}</span>
                  <span className="map-course-meta">
                    <Dot tone={COURSE_TONE[c.status]} />
                    {c.concept_count === 0 ? "nessun concetto in comune" : plural(c.concept_count, "concetto in comune", "concetti in comune")}
                  </span>
                </button>
              );
            })}
            {data.concepts.map((k) => {
              const b = lay.concepts.get(k.id)!;
              const cls = k.id === selected ? "sel" : focusSet.has(k.id) ? "focus" : focusCourse !== null ? "dim" : "";
              return (
                <button
                  key={k.id}
                  type="button"
                  className={`map-concept ${cls}`}
                  style={{ left: b.x, top: b.y, width: b.w }}
                  title={k.label}
                  aria-pressed={k.id === selected}
                  onClick={() => onConcept(k.id)}
                >
                  {k.label}
                </button>
              );
            })}
          </>
        )}
      </div>
      <Legend />
    </div>
  );
}

/** Phones: the graph doesn't fit, so a list of concepts that expand to their occurrences. */
function PhoneList({ data, courses }: { data: MapData; courses: Map<number, MapCourse> }) {
  const [open, setOpen] = useState<string | null>(data.concepts[0]?.id ?? null);
  return (
    <div className="map-list">
      {data.concepts.map((k) => {
        const isOpen = open === k.id;
        return (
          <div key={k.id} className={`card map-item ${isOpen ? "open" : ""}`}>
            <button type="button" className="map-item-head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : k.id)}>
              <span className="grow">
                <span className="map-item-title">{k.label}</span>
                <span className="map-item-courses">
                  {k.course_ids.map((id) => (
                    <span key={id}>
                      <Dot tone={COURSE_TONE[courses.get(id)?.status ?? "ok"]} />
                      {courses.get(id)?.name ?? `#${id}`}
                    </span>
                  ))}
                </span>
              </span>
              <Icon name={isOpen ? "chevron-up" : "chevron-down"} />
            </button>
            {isOpen && (
              <div className="map-occs">
                {k.occurrences.map((o) => (
                  <OccurrenceCard key={`${o.chapter_id}:${o.line}`} o={o} course={courses.get(o.course_id)} />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function ConceptMap() {
  const { data, error, loading, reload } = useApi(() => get<MapData>("/api/concepts"));
  // The map is recomputed server-side when sources change; refresh quietly while the page is open.
  usePoll(() => document.visibilityState === "visible" && reload(), 30000);
  const [selected, setSelected] = useState<string | null>(null);
  const [focusCourse, setFocusCourse] = useState<number | null>(null);
  const courses = useMemo(() => new Map((data?.courses ?? []).map((c) => [c.id, c])), [data]);

  useEffect(() => {
    if (data && !data.concepts.some((k) => k.id === selected)) setSelected(data.concepts[0]?.id ?? null);
  }, [data, selected]);

  if (!data) return loading ? <Loading /> : <ErrorBox error={error} />;
  if (data.concepts.length === 0) {
    return (
      <Empty icon="map">
        <strong>Nessun concetto in comune, per ora.</strong>
        <div className="map-empty-text">
          {data.courses.length < 2
            ? "Servono almeno due materie: la mappa collega le sezioni che trattano lo stesso argomento in materie diverse."
            : "La mappa si riempie da sola quando due materie hanno sezioni sullo stesso argomento (titoli simili o, con gli embeddings attivi, contenuti simili)."}
        </div>
        <a className="btn sm" href="/admin/courses">
          <Icon name="folder" />
          Vai alle materie
        </a>
      </Empty>
    );
  }

  const onCourse = (id: number) => {
    const next = focusCourse === id ? null : id;
    setFocusCourse(next);
    const sel = data.concepts.find((k) => k.id === selected);
    if (next !== null && !sel?.course_ids.includes(next)) {
      const first = data.concepts.find((k) => k.course_ids.includes(next));
      if (first) setSelected(first.id);
    }
  };

  return (
    <>
      <div className="map-layout hide-mobile">
        <Graph
          data={data}
          selected={selected}
          focusCourse={focusCourse}
          onConcept={(id) => {
            setSelected(id);
            setFocusCourse(null);
          }}
          onCourse={onCourse}
        />
        <Detail concept={data.concepts.find((k) => k.id === selected)} courses={courses} mode={data.mode} />
      </div>
      <div className="show-mobile map-phone">
        <PhoneList data={data} courses={courses} />
      </div>
    </>
  );
}
