// «Aggiungi al testo» for a lesson: asks for the subject's guidelines (or tells that the text will be generated automatically),
// then hands the lesson to the import.
import { useEffect, useState } from "react";
import { get, post } from "../../lib/api";
import { Icon } from "../icons";
import type { Course } from "../types";
import { Modal, toastError } from "../ui";
import GuidelinesField from "./GuidelinesField";

export interface LessonSummary {
  id: number;
  course_id: number;
  course_name: string;
  course_guidelines: string;
  /** The chapter this lesson's text already lives in (generating again updates it there). */
  chapter_id: number | null;
  slides: number;
  notes_pages: number;
  ink_pages: number;
}

const pages = (n: number) => (n === 1 ? "1 pagina" : `${n} pagine`);

export default function GenerateDialog({ lesson, beforeSend, onClose }: { lesson: LessonSummary; beforeSend?: () => Promise<unknown>; onClose: () => void }) {
  const [guidelines, setGuidelines] = useState(lesson.course_guidelines);
  const [save, setSave] = useState(true);
  // "update" = the chapter the lesson already lives in, integrated with what is there; "new" = a chapter of its own (one lesson,
  // one chapter); "auto" = Lecta compares with the chapters of the subject; else a chapter id.
  const [where, setWhere] = useState(lesson.chapter_id ? "update" : "new");
  const [course, setCourse] = useState<Course | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get<Course>(`/api/courses/${lesson.course_id}`).then(setCourse).catch(() => undefined);
  }, [lesson.course_id]);

  const go = async () => {
    setBusy(true);
    try {
      await beforeSend?.();
      const r = await post<{ job_id: number }>(`/api/lessons/${lesson.id}/generate`, {
        guidelines: guidelines.trim(),
        save_guidelines: save,
        chapter_id: /^\d+$/.test(where) ? Number(where) : null,
        placement: where === "auto" ? "auto" : where === "update" ? "update" : "new_chapter",
      });
      location.href = `/admin/jobs/${r.job_id}`;
    } catch (e) {
      toastError(e);
      setBusy(false);
    }
  };

  const own = (course?.chapters ?? []).find((ch) => ch.id === lesson.chapter_id);
  const nothingTyped = lesson.notes_pages === 0;
  return (
    <Modal
      title="Aggiungi gli appunti al testo della materia"
      onClose={onClose}
      wide
      actions={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Annulla
          </button>
          <button className="btn primary" onClick={go} disabled={busy} data-testid="generate-go">
            {busy && <Icon name="loader" className="spin" />}
            {guidelines.trim() ? "Aggiungi con queste linee guida" : "Aggiungi al testo"}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="small muted">
          Lecta parte dai tuoi appunti, li completa con le slide (comprese le tue scritte a mano) e ne fa un testo da studiare in «{lesson.course_name}».{" "}
          {[
            lesson.slides > 0 && `${lesson.slides} slide`,
            lesson.notes_pages > 0 && `appunti su ${pages(lesson.notes_pages)}`,
            lesson.ink_pages > 0 && `scrittura a mano su ${pages(lesson.ink_pages)}`,
          ]
            .filter(Boolean)
            .join(" · ")}
        </div>
        {nothingTyped && (
          <div className="alert warn small">
            <Icon name="alert-triangle" />
            <span className="grow">Non hai scritto appunti: il testo sarà un riassunto delle slide{lesson.ink_pages > 0 ? " e di ciò che hai scritto a mano" : ""}.</span>
          </div>
        )}
        <GuidelinesField value={guidelines} onChange={setGuidelines} subject={lesson.course_name} />
        <label className="check small">
          <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} />
          Ricorda queste linee guida per la materia (si cambiano anche nelle impostazioni)
        </label>
        <label className="field">
          Dove
          <select value={where} onChange={(e) => setWhere(e.target.value)} data-testid="generate-where">
            {own && (
              <option value="update">
                Aggiorna «{String(own.position).padStart(2, "0")} {own.title}» con le modifiche alla lezione
              </option>
            )}
            <option value="new">Un capitolo nuovo (una lezione = un capitolo)</option>
            <option value="auto">Decide Lecta (capitolo nuovo o esistente)</option>
            {(course?.chapters ?? []).filter((ch) => ch.id !== lesson.chapter_id).map((ch) => (
              <option key={ch.id} value={ch.id}>
                Continua «{String(ch.position).padStart(2, "0")} {ch.title}»
              </option>
            ))}
          </select>
        </label>
        {where === "update" && (
          <div className="small muted">
            Il testo di questa lezione in «{own?.title}» viene aggiornato al suo posto: ciò che c’è già (struttura, formule, figure e le tue aggiunte) resta,
            e cambia solo quello che hai modificato nella lezione. Il resto del capitolo non viene toccato.
          </div>
        )}
      </div>
    </Modal>
  );
}
