// The page of a course: its lessons and the chapters of its text, publishing and downloads. The text itself (draft,
// outline, assistant) opens from here at /admin/courses/{id}/testo.
import { useState } from "react";
import { fmtDate, get } from "../lib/api";
import { courseText } from "../lib/links";
import { Icon } from "./icons";
import LessonStatusPill, { type LessonStatus } from "./lessons/LessonStatus";
import { CoursePill } from "./pagekit";
import type { Course, TreeCourse } from "./types";
import { Empty, ErrorBox, Loading, Modal, toast, useApi } from "./ui";
import { compileFull } from "./workspace/compile";
import CourseSettings from "./workspace/CourseSettings";
import PublishControl from "./workspace/PublishControl";

interface LessonRow {
  id: number;
  number: number;
  title: string;
  status: LessonStatus;
  has_pdf: boolean;
  pdf_pages: number;
  page_count: number;
  generated_at: string | null;
  updated_at: string;
  lab: { files: number; comments: number } | null;
}

export default function CourseOverview({ courseId }: { courseId: number }) {
  const course = useApi(() => get<Course>(`/api/courses/${courseId}`), [courseId]);
  const status = useApi(() => get<TreeCourse[]>("/api/tree").then((t) => t.find((c) => c.id === courseId)?.status), [courseId]);
  const lessons = useApi(() => get<LessonRow[]>(`/api/lessons?course_id=${courseId}`), [courseId]);
  const [settings, setSettings] = useState(false);
  const [compiling, setCompiling] = useState(false);

  if (course.error) return <ErrorBox error={course.error} />;
  const c = course.data;
  if (!c) return <Loading />;
  const chapters = [...(c.chapters ?? [])].sort((a, b) => a.position - b.position);
  const labs = [...(lessons.data ?? [])].filter((l) => l.lab).sort((a, b) => a.number - b.number);
  const newLesson = `/admin/lessons?new=1&course=${courseId}`;

  const downloadPdf = async () => {
    if (compiling) return;
    const w = window.open("", "_blank"); // opened now, while the click still counts
    setCompiling(true);
    try {
      const r = await compileFull(courseId);
      if (r.pdf_url) {
        if (w) w.location.href = r.pdf_url;
        else window.location.href = r.pdf_url;
        if (r.status !== "ok") toast("Il PDF è stato compilato con errori: aprili dal testo della materia", "error");
      } else {
        w?.close();
        toast("Il PDF non si compila: apri il testo per vedere gli errori", "error");
      }
    } catch (e) {
      w?.close();
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setCompiling(false);
    }
  };

  return (
    <div className="co" data-testid="course-overview">
      <div className="page-head">
        <div className="grow">
          <h1>{c.name}</h1>
          <div className="sub row">
            <CoursePill status={status.data ?? undefined} size="sm" />
            {[c.academic_year, c.description].filter(Boolean).join(" · ")}
          </div>
        </div>
        <div className="row co-actions">
          <PublishControl courseId={c.id} courseName={c.name} onChanged={() => void course.reload()} />
          <button type="button" className="btn" onClick={downloadPdf} disabled={compiling} data-testid="overview-pdf">
            {compiling ? <Icon name="loader" className="spin" /> : <Icon name="download" />}
            PDF
          </button>
          <a className="btn" href={`/api/courses/${c.id}/source.zip`} download title="Sorgente LaTeX (.zip)">
            <Icon name="code" />
            LaTeX
          </a>
          <button type="button" className="btn ghost icon" onClick={() => setSettings(true)} aria-label="Impostazioni della materia" title="Impostazioni della materia">
            <Icon name="sliders" />
          </button>
          <a className="btn primary" href={courseText(c.id)}>
            <Icon name="book" />
            Apri il testo
          </a>
        </div>
      </div>

      <div className="co-grid">
        <section aria-labelledby="co-lessons">
          <div className="section-label">
            <span id="co-lessons">Lezioni</span>
            <a className="link" href={newLesson}>
              <Icon name="plus" />
              Nuova lezione
            </a>
          </div>
          {lessons.data === null ? (
            <Loading />
          ) : lessons.data.length === 0 ? (
            <Empty icon="notebook">
              Ancora nessuna lezione. <a href={newLesson}>Creane una</a>, prendi appunti sulle slide e poi aggiungili al testo.
            </Empty>
          ) : (
            <div className="card flush rows">
              {[...lessons.data]
                .sort((a, b) => a.number - b.number)
                .map((l) => (
                  <div key={l.id} className="pg-row co-row" data-testid="overview-lesson">
                    <span className="status-ico pg-kind-ico">
                      <Icon name="notebook" />
                    </span>
                    <div className="grow">
                      <a className="pg-row-title" href={`/admin/courses/${courseId}/lessons/${l.number}`}>
                        {l.title}
                      </a>
                      <div className="pg-row-sub">
                        {l.has_pdf ? `${l.pdf_pages} slide` : `${l.page_count} pagine`}
                        {l.generated_at ? ` · nel testo dal ${fmtDate(l.generated_at)}` : ` · modificata il ${fmtDate(l.updated_at)}`}
                      </div>
                    </div>
                    <LessonStatusPill
                      id={l.id}
                      status={l.status}
                      editable
                      onChange={(s) => lessons.setData((all) => all && all.map((x) => (x.id === l.id ? { ...x, status: s } : x)))}
                    />
                  </div>
                ))}
            </div>
          )}
        </section>

        <section aria-labelledby="co-chapters">
          <div className="section-label">
            <span id="co-chapters">Capitoli</span>
            <a className="link" href={courseText(c.id)}>
              Apri il testo <Icon name="chevron-right" />
            </a>
          </div>
          {chapters.length === 0 ? (
            <Empty icon="book">Ancora nessun capitolo: nasce quando integri gli appunti di una lezione.</Empty>
          ) : (
            <div className="card flush rows">
              {chapters.map((ch, i) => (
                <a key={ch.id} className="pg-row co-row co-chapter" href={courseText(c.id, { chapter: ch.id })} data-testid="overview-chapter">
                  <span className="co-num mono muted">{i + 1}</span>
                  <span className="grow pg-row-title">{ch.title}</span>
                  <Icon name="chevron-right" />
                </a>
              ))}
            </div>
          )}
        </section>
      </div>

      <section aria-labelledby="co-lab" data-testid="overview-lab">
        <div className="section-label">
          <span id="co-lab">Laboratori</span>
        </div>
        {lessons.data === null ? (
          <Loading />
        ) : labs.length === 0 ? (
          <Empty icon="flask">
            Ancora nessun laboratorio. Ogni lezione può averne uno: aprilo con «Laboratorio» dalla lezione, carica i file spiegati in classe e commentali.
          </Empty>
        ) : (
          <div className="card flush rows">
            {labs.map((l) => (
              <div key={l.id} className="pg-row co-row" data-testid="overview-lab-row">
                <span className="status-ico pg-kind-ico">
                  <Icon name="flask" />
                </span>
                <div className="grow">
                  <a className="pg-row-title" href={`/admin/courses/${courseId}/lessons/${l.number}/lab`}>
                    Laboratorio · {l.title}
                  </a>
                  <div className="pg-row-sub">
                    {l.lab!.files === 1 ? "1 file" : `${l.lab!.files} file`} · {l.lab!.comments === 1 ? "1 commento" : `${l.lab!.comments} commenti`}
                  </div>
                </div>
                <a className="link small" href={`/admin/courses/${courseId}/lessons/${l.number}`} title="Apri la lezione teorica">
                  <Icon name="notebook" /> Lezione {l.number}
                </a>
              </div>
            ))}
          </div>
        )}
      </section>

      {settings && (
        <Modal title="Impostazioni della materia" wide onClose={() => setSettings(false)}>
          <CourseSettings
            course={c}
            onSaved={() => {
              window.dispatchEvent(new Event("lecta:tree-changed"));
              void course.reload();
            }}
          />
        </Modal>
      )}
    </div>
  );
}
