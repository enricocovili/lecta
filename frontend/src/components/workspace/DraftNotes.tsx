// Notes on the draft. A reader's (decision 129): the box where a comment or a suggested change to a block is written,
// and their notes, under their block or, when its text changed, at the end of the chapter. The author's (decision 130):
// the readers' open notes, under their block or in the chapter's list, to accept, reject or resolve.
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, del, get, patch, post } from "../../lib/api";
import { Icon } from "../icons";
import { Confirm, fmtWhen, toast, toastError } from "../ui";
import { EditBar, EditBox } from "./EditBox";
import { NOTE_KIND, NOTE_STATUS, noteProblem, wordDiff, type DraftNote, type NoteKind } from "./notes";

/** The notes on the course the user may see (a reader: their own; its writers: everyone's), kept up to date by what is
 *  done here and fetched again with each new `revision` of the text (an open note follows its block). */
export function useDraftNotes(courseId: number, enabled: boolean, revision = 0) {
  const [notes, setNotes] = useState<DraftNote[]>([]);
  const [asked, setAsked] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    get<DraftNote[]>(`/api/courses/${courseId}/notes`)
      .then((ns) => live && setNotes(ns))
      .catch(() => undefined); // without them the draft still reads: no markers
    return () => {
      live = false;
    };
  }, [courseId, enabled, revision, asked]);
  const saved = useCallback((n: DraftNote) => setNotes((ns) => (ns.some((x) => x.id === n.id) ? ns.map((x) => (x.id === n.id ? n : x)) : [...ns, n])), []);
  const removed = useCallback((id: number) => setNotes((ns) => ns.filter((x) => x.id !== id)), []);
  const reload = useCallback(() => setAsked((a) => a + 1), []);
  return { notes, saved, removed, reload };
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

/** A suggestion's change to its block, word by word: what goes out struck through, what comes in underlined. */
function NoteDiff({ from, to }: { from: string; to: string }) {
  const parts = useMemo(() => wordDiff(from, to), [from, to]);
  return (
    <pre className="doc-note-proposed doc-note-diff" aria-label="Modifica proposta" data-testid="note-diff">
      {parts.map((p, i) => (p.op === "same" ? <span key={i}>{p.text}</span> : p.op === "del" ? <del key={i}>{p.text}</del> : <ins key={i}>{p.text}</ins>))}
    </pre>
  );
}

type Closing = "accept" | "reject" | "resolve";
const CLOSED: Record<Closing, string> = { accept: "Suggerimento accettato: il blocco è cambiato", reject: "Suggerimento rifiutato", resolve: "Commento risolto" };

function NoteCard({
  note,
  withSrc,
  writer,
  busy,
  onEdit,
  onDelete,
  onClose,
}: {
  note: DraftNote;
  withSrc: boolean;
  /** Seen by whoever may change the course: who wrote it, the change as a diff, and the buttons that close it. */
  writer: boolean;
  busy: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onClose: (how: Closing) => void;
}) {
  const tone = note.status === "open" ? "" : note.status === "accepted" ? "ok" : note.status === "rejected" ? "danger" : "accent";
  const open = note.status === "open";
  return (
    <div className="doc-note" data-testid="draft-note" data-status={note.status}>
      <div className="doc-note-head">
        <Icon name={note.kind === "suggestion" ? "pencil" : "message"} />
        <strong>{NOTE_KIND[note.kind]}</strong>
        {writer && note.author && <span className="small">di {note.author}</span>}
        <span className={`pill sm ${tone}`} data-testid="note-status">
          {NOTE_STATUS[note.status]}
          {note.resolved_by ? ` da ${note.resolved_by}` : ""}
        </span>
        {note.changed && <span className="pill sm warn">testo cambiato</span>}
        <span className="muted tiny">{fmtWhen(note.updated_at)}</span>
      </div>
      {withSrc && !(writer && note.proposed !== null) && (
        <pre className="doc-note-src" title={writer ? "Il blocco com'era quando è stata scritta la nota" : "Il blocco com'era quando hai scritto la nota"}>
          {note.src}
        </pre>
      )}
      {note.body && <p className="doc-note-body">{note.body}</p>}
      {note.proposed !== null &&
        (writer ? (
          <NoteDiff from={note.src} to={note.proposed} />
        ) : (
          <pre className="doc-note-proposed" aria-label="Testo proposto">
            {note.proposed}
          </pre>
        ))}
      {writer && open && (
        <div className="doc-note-actions">
          {note.kind === "suggestion" ? (
            <>
              <button
                type="button"
                className="btn primary sm"
                disabled={busy || note.changed}
                title={note.changed ? "Il blocco è cambiato da quando è stato suggerito: si può solo rifiutare, o riscrivere a mano" : "Il testo proposto prende il posto del blocco"}
                onClick={() => onClose("accept")}
              >
                <Icon name="check" /> Accetta
              </button>
              <button type="button" className="btn ghost sm" disabled={busy} onClick={() => onClose("reject")}>
                <Icon name="x" /> Rifiuta
              </button>
            </>
          ) : (
            <button type="button" className="btn ghost sm" disabled={busy} onClick={() => onClose("resolve")}>
              <Icon name="check" /> Risolvi
            </button>
          )}
        </div>
      )}
      {!writer && note.mine && open && (
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

/** Notes, under their block or at the end of the chapter: a reader changes or deletes theirs while open; whoever may
 *  change the course accepts or rejects a suggestion (accepted, its text replaces the block) and resolves a comment. */
export function NoteList({
  notes,
  title,
  withSrc = false,
  writer = false,
  testId = "block-notes",
  onSaved,
  onRemoved,
  onAccepted,
  onStale,
  onClose,
}: {
  notes: DraftNote[];
  title: string;
  /** Show the block each note was written on (it is no longer in the draft, or the list isn't next to it). */
  withSrc?: boolean;
  /** The course's writer's list: the readers' notes to close. */
  writer?: boolean;
  testId?: string;
  onSaved: (n: DraftNote) => void;
  onRemoved: (id: number) => void;
  /** A suggestion went into the text: its chapter is typeset again. */
  onAccepted?: (n: DraftNote) => Promise<void>;
  /** The note couldn't be closed as shown (its block changed, someone else closed it): the notes and the text again. */
  onStale?: (n: DraftNote) => void;
  onClose?: () => void;
}) {
  const [editing, setEditing] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<DraftNote | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const close = async (n: DraftNote, how: Closing) => {
    setBusy(n.id);
    try {
      const done = await post<DraftNote>(`/api/notes/${n.id}/${how}`);
      if (how === "accept") await onAccepted?.(done).catch(() => undefined); // written: the draft catches up anyway
      onSaved(done);
      toast(CLOSED[how]);
    } catch (e) {
      toastError(e);
      if (e instanceof ApiError && e.status === 409) onStale?.(n);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="doc-notes" data-testid={testId} role="region" aria-label={title}>
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
          <NoteCard
            key={n.id}
            note={n}
            withSrc={withSrc}
            writer={writer}
            busy={busy === n.id}
            onEdit={() => setEditing(n.id)}
            onDelete={() => setDeleting(n)}
            onClose={(how) => void close(n, how)}
          />
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
