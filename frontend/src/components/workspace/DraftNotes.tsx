// A reader's notes on the draft (decision 129): the box where a comment or a suggested change to a block is written,
// and the notes themselves, under their block or, when its text changed, at the end of the chapter.
import { useCallback, useEffect, useState } from "react";
import { del, get, patch } from "../../lib/api";
import { Icon } from "../icons";
import { Confirm, fmtWhen } from "../ui";
import { EditBar, EditBox } from "./EditBox";
import { NOTE_KIND, NOTE_STATUS, noteProblem, type DraftNote, type NoteKind } from "./notes";

/** The notes on the course the user may see (a reader: their own), kept up to date by what they write here. */
export function useDraftNotes(courseId: number, enabled: boolean) {
  const [notes, setNotes] = useState<DraftNote[]>([]);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    get<DraftNote[]>(`/api/courses/${courseId}/notes`)
      .then((ns) => live && setNotes(ns))
      .catch(() => undefined); // without them the draft still reads: no markers
    return () => {
      live = false;
    };
  }, [courseId, enabled]);
  const saved = useCallback((n: DraftNote) => setNotes((ns) => (ns.some((x) => x.id === n.id) ? ns.map((x) => (x.id === n.id ? n : x)) : [...ns, n])), []);
  const removed = useCallback((id: number) => setNotes((ns) => ns.filter((x) => x.id !== id)), []);
  return { notes, saved, removed };
}

/** A comment (words) or a suggestion (the block's LaTeX, changed, and why): Ctrl+Enter sends it, Esc leaves it. */
export function NoteComposer({
  kind,
  src,
  note,
  onSend,
  onCancel,
}: {
  kind: NoteKind;
  /** The block's LaTeX, where a suggestion starts from. */
  src: string;
  /** The note being changed, if it is one. */
  note?: DraftNote;
  onSend: (body: string, proposed: string | null) => Promise<void>;
  onCancel: () => void;
}) {
  const [body, setBody] = useState(note?.body ?? "");
  const [proposed, setProposed] = useState(note?.proposed ?? src);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const suggestion = kind === "suggestion";

  const send = async () => {
    if (busy) return;
    const problem = noteProblem(kind, body, proposed, note ? undefined : src);
    if (problem) return setError(problem);
    setBusy(true);
    setError(null);
    try {
      await onSend(body.trim(), suggestion ? proposed : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };
  const submit = () => void send();

  return (
    <div className="doc-edit doc-note-edit" data-testid="note-editor">
      <div className="doc-note-edit-head small">
        <Icon name={suggestion ? "pencil" : "message"} />
        {note ? `Modifica ${suggestion ? "il suggerimento" : "il commento"}` : suggestion ? "Suggerisci una modifica a questo blocco" : "Commenta questo blocco"}
      </div>
      {suggestion ? (
        <>
          <EditBox value={proposed} onChange={setProposed} label="Testo proposto" disabled={busy} autoFocus onSubmit={submit} onCancel={onCancel} />
          <EditBox value={body} onChange={setBody} label="Motivo" placeholder="Perché (facoltativo)" prose disabled={busy} onSubmit={submit} onCancel={onCancel} />
        </>
      ) : (
        <EditBox value={body} onChange={setBody} label="Commento" placeholder="Cosa non torna, cosa non è chiaro…" prose disabled={busy} autoFocus onSubmit={submit} onCancel={onCancel} />
      )}
      <EditBar
        hint={`Ctrl+Invio ${note ? "salva" : "invia"} · Esc annulla · lo vede solo chi scrive la materia`}
        error={error}
        busy={busy}
        submitLabel={note ? "Salva" : "Invia"}
        onSubmit={submit}
        onCancel={onCancel}
      />
    </div>
  );
}

function NoteCard({ note, withSrc, onEdit, onDelete }: { note: DraftNote; withSrc: boolean; onEdit: () => void; onDelete: () => void }) {
  const tone = note.status === "open" ? "" : note.status === "accepted" ? "ok" : note.status === "rejected" ? "danger" : "accent";
  return (
    <div className="doc-note" data-testid="draft-note" data-status={note.status}>
      <div className="doc-note-head">
        <Icon name={note.kind === "suggestion" ? "pencil" : "message"} />
        <strong>{NOTE_KIND[note.kind]}</strong>
        <span className={`pill sm ${tone}`} data-testid="note-status">
          {NOTE_STATUS[note.status]}
          {note.resolved_by ? ` da ${note.resolved_by}` : ""}
        </span>
        {note.changed && <span className="pill sm warn">testo cambiato</span>}
        <span className="muted tiny">{fmtWhen(note.updated_at)}</span>
      </div>
      {withSrc && (
        <pre className="doc-note-src" title="Il blocco com'era quando hai scritto la nota">
          {note.src}
        </pre>
      )}
      {note.body && <p className="doc-note-body">{note.body}</p>}
      {note.proposed !== null && (
        <pre className="doc-note-proposed" aria-label="Testo proposto">
          {note.proposed}
        </pre>
      )}
      {note.mine && note.status === "open" && (
        <div className="doc-note-actions">
          <button type="button" className="btn ghost sm" onClick={onEdit}>
            <Icon name="pencil" /> Modifica
          </button>
          <button type="button" className="btn ghost sm" onClick={onDelete}>
            <Icon name="trash" /> Elimina
          </button>
        </div>
      )}
    </div>
  );
}

/** Notes, under their block or at the end of the chapter: each one changed in place, or deleted, while open. */
export function NoteList({
  notes,
  title,
  withSrc = false,
  onSaved,
  onRemoved,
  onClose,
}: {
  notes: DraftNote[];
  title: string;
  /** Show the block each note was written on (it is no longer in the draft). */
  withSrc?: boolean;
  onSaved: (n: DraftNote) => void;
  onRemoved: (id: number) => void;
  onClose?: () => void;
}) {
  const [editing, setEditing] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<DraftNote | null>(null);
  return (
    <div className="doc-notes" data-testid="block-notes" role="region" aria-label={title}>
      <div className="doc-notes-head small">
        <span>{title}</span>
        {onClose && (
          <button type="button" className="btn ghost icon sm" aria-label="Chiudi le note" onClick={onClose}>
            <Icon name="x" />
          </button>
        )}
      </div>
      {notes.map((n) =>
        editing === n.id ? (
          <NoteComposer
            key={n.id}
            kind={n.kind}
            src={n.src}
            note={n}
            onCancel={() => setEditing(null)}
            onSend={async (body, proposed) => {
              onSaved(await patch<DraftNote>(`/api/notes/${n.id}`, { body, ...(proposed !== null ? { proposed } : {}) }));
              setEditing(null);
            }}
          />
        ) : (
          <NoteCard key={n.id} note={n} withSrc={withSrc} onEdit={() => setEditing(n.id)} onDelete={() => setDeleting(n)} />
        ),
      )}
      {deleting && (
        <Confirm
          title={`Elimina ${deleting.kind === "suggestion" ? "il suggerimento" : "il commento"}`}
          danger
          message="La nota sparisce anche per chi scrive la materia."
          confirmLabel="Elimina"
          onConfirm={async () => {
            await del(`/api/notes/${deleting.id}`);
            onRemoved(deleting.id);
          }}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
