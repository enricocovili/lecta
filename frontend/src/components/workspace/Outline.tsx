// The left rail: the outline of the document (chapters and their sections), the structure actions that need
// no AI (add, rename, reorder, delete), and the sources of the course.
import { useState } from "react";
import { del, fmtSize, get, patch, post } from "../../lib/api";
import { Icon } from "../icons";
import { kindIcon } from "../pagekit";
import { Confirm, toastError, useApi } from "../ui";
import Pop from "./Pop";
import type { CourseChapter, DraftChapter } from "./types";

interface SourceRow {
  id: number;
  name: string;
  kind: string;
  size: number;
  pages: number | null;
}

function ChapterItem({
  ch,
  n,
  first,
  last,
  active,
  open,
  changed,
  sections,
  courseId,
  onGoto,
  onToggle,
  onMove,
  onAsk,
  onStructure,
}: {
  ch: CourseChapter;
  n: number;
  first: boolean;
  last: boolean;
  active: boolean;
  open: boolean;
  changed: boolean;
  sections: DraftChapter["toc"];
  courseId: number;
  onGoto: (sectionId?: string) => void;
  onToggle: () => void;
  onMove: (dir: -1 | 1) => void;
  onAsk: () => void;
  onStructure: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(ch.title);
  const [confirmDel, setConfirmDel] = useState(false);
  const save = async () => {
    if (!title.trim() || title.trim() === ch.title) return setEditing(false);
    try {
      await patch(`/api/courses/${courseId}/chapters/${ch.id}`, { title: title.trim() });
      setEditing(false);
      onStructure();
    } catch (e) {
      toastError(e);
    }
  };
  const subs = sections.filter((s) => s.level <= 2);
  return (
    <li className={`rail-ch ${active ? "active" : ""}`} data-chapter={ch.id}>
      <div className="rail-ch-row">
        <button type="button" className="rail-caret" onClick={onToggle} aria-label={open ? "Comprimi le sezioni" : "Mostra le sezioni"} aria-expanded={open} disabled={subs.length === 0}>
          <Icon name={open ? "chevron-down" : "chevron-right"} />
        </button>
        {editing ? (
          <form
            className="rail-rename"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus aria-label="Titolo del capitolo" onKeyDown={(e) => e.key === "Escape" && setEditing(false)} />
          </form>
        ) : (
          <button type="button" className="rail-ch-title" onClick={() => onGoto()} title={ch.title}>
            <span className="rail-n mono">{n}</span>
            <span className="rail-t">{ch.title}</span>
            {changed && <span className="rail-ai" title="Modificato di recente dall’AI" aria-label="Modificato di recente dall’AI" />}
          </button>
        )}
        {!editing && (
          <Pop label={`Azioni sul capitolo ${ch.title}`} className="ghost icon xs" menuClass="rail-menu" summary={<Icon name="more" />}>
            <button type="button" onClick={onAsk}>
              <Icon name="sparkles" />
              Chiedi all’AI su questo capitolo
            </button>
            <button
              type="button"
              onClick={() => {
                setTitle(ch.title);
                setEditing(true);
              }}
            >
              <Icon name="pencil" />
              Rinomina
            </button>
            {!first && (
              <button type="button" onClick={() => onMove(-1)}>
                <Icon name="chevron-up" />
                Sposta su
              </button>
            )}
            {!last && (
              <button type="button" onClick={() => onMove(1)}>
                <Icon name="chevron-down" />
                Sposta giù
              </button>
            )}
            <button type="button" className="pg-danger" onClick={() => setConfirmDel(true)}>
              <Icon name="trash" />
              Elimina
            </button>
          </Pop>
        )}
      </div>
      {open && subs.length > 0 && (
        <ul className="rail-secs">
          {subs.map((s, i) => (
            <li key={`${s.id}-${i}`}>
              <button type="button" className={`rail-sec l${s.level}`} onClick={() => onGoto(s.id)} title={s.title}>
                {s.title}
              </button>
            </li>
          ))}
        </ul>
      )}
      {confirmDel && (
        <Confirm
          title="Elimina capitolo"
          danger
          confirmLabel="Elimina"
          message={
            <>
              Eliminare <strong>{ch.title}</strong>? Il capitolo viene tolto dal documento. Puoi tornare indietro solo chiedendolo all’AI.
            </>
          }
          onConfirm={async () => {
            await del(`/api/courses/${courseId}/chapters/${ch.id}`);
            onStructure();
          }}
          onClose={() => setConfirmDel(false)}
        />
      )}
    </li>
  );
}

function Sources({ courseId }: { courseId: number }) {
  const { data } = useApi(() => get<SourceRow[]>(`/api/courses/${courseId}/sources`).catch(() => [] as SourceRow[]), [courseId]);
  const [open, setOpen] = useState(false);
  const rows = data ?? [];
  return (
    <div className="rail-block">
      <button type="button" className="rail-label rail-label-btn" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Icon name={open ? "chevron-down" : "chevron-right"} />
        Fonti
        <span className="rail-count mono">{rows.length}</span>
      </button>
      {open && (
        <ul className="rail-sources">
          {rows.length === 0 && <li className="rail-none tiny muted">Ancora nessuna fonte collegata.</li>}
          {rows.map((s) => (
            <li key={s.id}>
              <a href={`/admin/sources/${s.id}`} target="_blank" rel="noreferrer" title={`${s.name} · ${fmtSize(s.size)}`}>
                <Icon name={kindIcon(s.kind)} />
                <span className="ellipsis">{s.name}</span>
                {s.pages ? <span className="tiny muted mono">{s.pages}p</span> : null}
              </a>
            </li>
          ))}
        </ul>
      )}
      <a className="btn sm rail-import" href={`/admin/lessons?new=1&course=${courseId}`}>
        <Icon name="plus" />
        Nuova lezione
      </a>
    </div>
  );
}

export default function Outline({
  courseId,
  chapters,
  preview,
  activeChapterId,
  aiChanged,
  onGoto,
  onAskChapter,
  onStructure,
}: {
  courseId: number;
  chapters: CourseChapter[];
  preview: DraftChapter[] | null;
  activeChapterId: number | null;
  aiChanged: Set<number>;
  onGoto: (chapterId: number, sectionId?: string) => void;
  onAskChapter: (chapterId: number) => void;
  /** A chapter was added, renamed, moved or deleted: reload the course and the draft. */
  onStructure: () => void;
}) {
  const [openMap, setOpenMap] = useState<Record<number, boolean>>({});
  const [adding, setAdding] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const toc = new Map((preview ?? []).map((p) => [p.chapter.id, p.toc]));

  const structure = () => {
    window.dispatchEvent(new Event("lecta:tree-changed"));
    onStructure();
  };
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    const t = newTitle.trim();
    if (!t) return;
    try {
      await post(`/api/courses/${courseId}/chapters`, { title: t });
      setNewTitle("");
      setAdding(false);
      structure();
    } catch (err) {
      toastError(err);
    }
  };
  const move = async (idx: number, dir: -1 | 1) => {
    const ids = chapters.map((c) => c.id);
    const j = idx + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[idx], ids[j]] = [ids[j], ids[idx]];
    try {
      await post(`/api/courses/${courseId}/chapters/reorder`, { ids });
      structure();
    } catch (e) {
      toastError(e);
    }
  };

  return (
    <nav className="rail" aria-label="Struttura del documento">
      <div className="rail-scroll">
        <div className="rail-label">Indice</div>
        {chapters.length === 0 && <p className="rail-none small muted">Nessun capitolo. Aggiungine uno o chiedi all’AI di iniziare.</p>}
        <ol className="rail-list">
          {chapters.map((ch, i) => (
            <ChapterItem
              key={ch.id}
              ch={ch}
              n={i + 1}
              first={i === 0}
              last={i === chapters.length - 1}
              active={ch.id === activeChapterId}
              open={openMap[ch.id] ?? ch.id === activeChapterId}
              changed={aiChanged.has(ch.id)}
              sections={toc.get(ch.id) ?? []}
              courseId={courseId}
              onGoto={(sid) => onGoto(ch.id, sid)}
              onToggle={() => setOpenMap({ ...openMap, [ch.id]: !(openMap[ch.id] ?? ch.id === activeChapterId) })}
              onMove={(d) => move(i, d)}
              onAsk={() => onAskChapter(ch.id)}
              onStructure={structure}
            />
          ))}
        </ol>
        {adding ? (
          <form className="rail-add" onSubmit={add}>
            <input type="text" value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="Titolo del capitolo" aria-label="Titolo del nuovo capitolo" autoFocus onKeyDown={(e) => e.key === "Escape" && setAdding(false)} />
            <button className="btn sm primary" disabled={!newTitle.trim()}>
              Aggiungi
            </button>
          </form>
        ) : (
          <button type="button" className="rail-addbtn" onClick={() => setAdding(true)}>
            <Icon name="plus" />
            Aggiungi capitolo
          </button>
        )}
        <Sources courseId={courseId} />
      </div>
    </nav>
  );
}
