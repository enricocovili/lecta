// The laboratory of a lesson: the files explained in class, in a tree on the left and open in the middle (code with its
// colours, pictures; PDFs and other files to download). Files come in from a picker, a folder or a drop, several at once.
// Nothing is ever run: text is shown as text.
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { ApiError, api, del, fmtSize, get, patch, post, uploadRaw } from "../../lib/api";
import { Icon } from "../icons";
import NotesField from "../lessons/NotesField";
import { Markdown } from "../workspace/Markdown";
import { Confirm, Empty, Loading, Modal, toast, toastError } from "../ui";
import CodeView, { type Lines, type Mark, type Moved } from "./CodeView";
import { MAX_FILE_BYTES, baseName, fileIcon, fromDrop, fromInput, tree, worthUploading, type Folder, type LabAccess, type LabData, type LabFile, type Picked } from "./files";
import NotebookView from "./NotebookView";
import PdfPages from "./PdfPages";
import { cellOf, isLines, pageOf, useLabStore, type LabComment, type SaveState } from "./useLabStore";

type Store = ReturnType<typeof useLabStore>;

const SAVE_LABEL: Record<SaveState, string> = { saved: "Salvato", pending: "Da salvare", saving: "Salvataggio…", offline: "Non salvato · riprovo" };

export default function LabPage({ courseId, number }: { courseId: number; number: number }) {
  const [lab, setLab] = useState<LabData | null>(null);
  const [missing, setMissing] = useState<{ id: number; title: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setLab(await get<LabData>(`/api/courses/${courseId}/lessons/${number}/lab`));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        const lesson = await get<{ id: number; title: string }>(`/api/courses/${courseId}/lessons/${number}?pages=false`).catch(() => null);
        if (lesson) return setMissing(lesson);
      }
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [courseId, number]);
  useEffect(() => void load(), [load]);

  if (error) return <div className="lab lab-loading"><div className="alert danger">{error}</div></div>;
  if (missing && !lab) {
    return (
      <div className="lab lab-loading">
        <div className="card lab-create">
          <span className="status-ico pg-kind-ico"><Icon name="flask" /></span>
          <h2>Laboratorio di «{missing.title}»</h2>
          <p className="muted">Qui carichi i file spiegati in laboratorio (sorgenti, notebook, PDF) e li commenti durante la lezione.</p>
          <div className="row">
            <a className="btn ghost" href={`/admin/courses/${courseId}/lessons/${number}`}>Torna alla lezione</a>
            <button
              type="button"
              className="btn primary"
              data-testid="lab-create"
              onClick={() => post<LabData>(`/api/lessons/${missing.id}/lab`).then(setLab, toastError)}
            >
              <Icon name="plus" /> Crea il laboratorio
            </button>
          </div>
        </div>
      </div>
    );
  }
  if (!lab) return <div className="lab lab-loading"><Loading /></div>;
  return <LabEditor lab={lab} access="owner" base={`/api/lessons/${lab.lesson.id}`} lessonHref={`/admin/courses/${courseId}/lessons/${number}`} />;
}

/** What is open: a file or the lab's notes, kept in the address (`?file=src/main.c`, `?notes`) so a link or a reload opens it again. */
function useOpenFile(files: LabFile[]) {
  const [where, setWhere] = useState<{ path: string | null; notes: boolean }>(() => {
    const q = new URLSearchParams(location.search);
    return { path: q.get("file"), notes: q.has("notes") };
  });
  const open = where.notes ? null : (files.find((f) => f.path === where.path) ?? null);
  const go = useCallback((path: string | null, notes: boolean) => {
    setWhere({ path, notes });
    const url = new URL(location.href);
    url.searchParams.delete("file");
    url.searchParams.delete("notes");
    if (notes) url.searchParams.set("notes", "");
    else if (path) url.searchParams.set("file", path);
    history.replaceState(null, "", url.href.replace(/notes=(&|$)/, "notes$1"));
  }, []);
  const choose = useCallback((p: string | null) => go(p, false), [go]);
  const showNotes = useCallback(() => go(null, true), [go]);
  return { open, notes: where.notes, choose, showNotes };
}

export function LabEditor({ lab, access, base, lessonHref }: { lab: LabData; access: LabAccess; base: string; lessonHref: string | null }) {
  const owner = access === "owner";
  const canEdit = access !== "read";
  const [files, setFiles] = useState<LabFile[]>(lab.files);
  const { open, notes: notesOpen, choose, showNotes } = useOpenFile(files);
  const [uploading, setUploading] = useState<{ name: string; done: number; total: number; fraction: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [renaming, setRenaming] = useState<LabFile | null>(null);
  const [removing, setRemoving] = useState<LabFile | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const root = useMemo(() => tree(files), [files]);
  // A file read again from the server (uploaded again, or changed elsewhere while edited here) is opened anew.
  const [reloads, setReloads] = useState<Record<number, number>>({});
  const storeRef = useRef<Store | null>(null);
  const reloadFile = useCallback(
    async (fileId: number) => {
      try {
        const fresh = await get<LabData>(`${base}/lab`);
        setFiles(fresh.files);
        const f = fresh.files.find((x) => x.id === fileId);
        if (f) storeRef.current?.adopt(fileId, f.version, fresh.comments);
        setReloads((r) => ({ ...r, [fileId]: (r[fileId] ?? 0) + 1 }));
      } catch (e) {
        toastError(e);
      }
    },
    [base],
  );
  const store = useLabStore(base, String(lab.id), lab.comments, { text: lab.notes, version: lab.notes_version }, lab.files, {
    onFileSaved: (f) => setFiles((cur) => cur.map((x) => (x.id === f.id ? { ...x, version: f.version, size: f.size } : x))),
    onFileConflict: (id) => void reloadFile(id),
  });
  storeRef.current = store;
  const perFile = useMemo(() => {
    const m = new Map<number, number>();
    for (const c of store.comments) m.set(c.file_id, (m.get(c.file_id) ?? 0) + 1);
    return m;
  }, [store.comments]);
  const save = useCallback(async () => {
    if (!(await store.flushAll())) toastError(new Error("Non riesco a salvare: riprovo appena torna la connessione"));
  }, [store]);

  // Ctrl+S saves the comments everywhere, also while typing, instead of the browser's "save page".
  useEffect(() => {
    if (!canEdit) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canEdit, save]);

  // The first file opens by itself when nothing is chosen.
  useEffect(() => {
    if (!open && !notesOpen && files.length) choose(files[0].path);
  }, [open, notesOpen, files, choose]);

  const upload = useCallback(
    async (picked: Picked[]) => {
      const list = picked.filter((p) => worthUploading(p.path));
      const big = list.filter((p) => p.file.size > MAX_FILE_BYTES);
      if (big.length) toast(`${big.map((p) => p.path).join(", ")}: oltre i 20 MB, non caricat${big.length > 1 ? "i" : "o"}`, "error");
      const ok = list.filter((p) => p.file.size <= MAX_FILE_BYTES);
      let last: string | null = null;
      for (const [i, p] of ok.entries()) {
        setUploading({ name: p.path, done: i, total: ok.length, fraction: 0 });
        try {
          const f = await uploadRaw<LabFile & { replaced: boolean }>(`${base}/lab/files?path=${encodeURIComponent(p.path)}`, p.file, (fraction) =>
            setUploading({ name: p.path, done: i, total: ok.length, fraction }),
          );
          const { replaced, ...file } = f;
          setFiles((cur) => [...cur.filter((x) => x.id !== file.id), file]);
          if (replaced) {
            toast(`«${file.path}» sostituito con il nuovo file`);
            await reloadFile(file.id); // its comments followed their lines on the server
          }
          last = file.path;
        } catch (e) {
          toastError(e);
        }
      }
      setUploading(null);
      if (last) choose(last);
    },
    [base, choose, reloadFile],
  );

  const onDrop = async (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (!canEdit) return;
    void upload(await fromDrop(e.dataTransfer));
  };

  const rename = async (f: LabFile, path: string) => {
    try {
      const out = await patch<LabFile>(`${base}/lab/files/${f.id}`, { path });
      setFiles((cur) => cur.map((x) => (x.id === f.id ? out : x)));
      if (open?.id === f.id) choose(out.path);
      setRenaming(null);
    } catch (e) {
      toastError(e);
    }
  };

  const remove = async (f: LabFile) => {
    await del(`${base}/lab/files/${f.id}`).catch(toastError);
    setFiles((cur) => cur.filter((x) => x.id !== f.id));
    store.forgetFile(f.id);
    if (open?.id === f.id) choose(null);
  };

  return (
    <div
      className={`lab ${dragging ? "dragging" : ""}`}
      data-testid="lab-editor"
      onDragOver={(e) => {
        if (!canEdit || !e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)}
      onDrop={onDrop}
    >
      <header className="wsb">
        {lessonHref && (
          <a className="btn ghost icon wsb-back" href={lessonHref} aria-label="Torna alla lezione" title="Torna alla lezione">
            <Icon name="arrow-left" />
          </a>
        )}
        <div className="lab-heading">
          <span className="lab-kicker"><Icon name="flask" /> Laboratorio</span>
          <span className="lab-title" data-testid="lab-title">{lab.lesson.title}</span>
        </div>
        <span className="les-course muted small">{lab.lesson.course_name}</span>
        <div className="wsb-actions">
          {canEdit && (
            <button type="button" className={`btn ghost icon les-save ${store.saveState}`} data-testid="lab-save-state" role="status" aria-live="polite" onClick={() => void save()} title={`${SAVE_LABEL[store.saveState]}. Salva ora (Ctrl+S); il salvataggio automatico avviene ogni minuto.`}>
              <Icon name={store.saveState === "saved" ? "check" : store.saveState === "saving" ? "loader" : store.saveState === "pending" ? "save" : "alert-triangle"} className={store.saveState === "saving" ? "spin" : ""} />
              <span className="sr-only">{SAVE_LABEL[store.saveState]}</span>
            </button>
          )}
          {lessonHref && (
            <a className="btn ghost" href={lessonHref} data-testid="lab-lesson" title="Apri la lezione teorica di questo laboratorio">
              <Icon name="notebook" />
              <span className="wsb-lbl">Lezione teorica</span>
            </a>
          )}
          {owner && (
            <span className="pill sm lab-publish" title="Il laboratorio è privato: la pubblicazione sul sito arriverà più avanti" data-testid="lab-publish">
              <Icon name="globe" /> Pubblicazione: in sviluppo
            </span>
          )}
          {canEdit && (
            <>
              <button type="button" className="btn" onClick={() => fileInput.current?.click()} disabled={!!uploading} data-testid="lab-upload">
                <Icon name="upload" />
                <span className="wsb-lbl">Carica file</span>
              </button>
              <button type="button" className="btn ghost icon" onClick={() => folderInput.current?.click()} disabled={!!uploading} aria-label="Carica una cartella" title="Carica una cartella (con le sue sottocartelle)">
                <Icon name="folder" />
              </button>
              <input ref={fileInput} type="file" multiple hidden data-testid="lab-file-input" onChange={(e) => { void upload(fromInput(e.target.files)); e.target.value = ""; }} />
              <input
                ref={folderInput}
                type="file"
                multiple
                hidden
                {...({ webkitdirectory: "" } as Record<string, string>)}
                onChange={(e) => { void upload(fromInput(e.target.files)); e.target.value = ""; }}
              />
            </>
          )}
          <button type="button" className="btn ghost icon" data-theme-toggle aria-label="Cambia tema" title="Tema chiaro / scuro">
            <Icon name="moon" className="i-moon" />
            <Icon name="sun" className="i-sun" />
          </button>
        </div>
      </header>

      {uploading && (
        <div className="lab-progress" role="status">
          <Icon name="loader" className="spin" /> Carico «{uploading.name}» ({uploading.done + 1} di {uploading.total})
          <span className="lab-progress-bar"><span style={{ width: `${Math.round(uploading.fraction * 100)}%` }} /></span>
        </div>
      )}

      <div className="lab-body">
        <aside className="lab-tree" aria-label="File del laboratorio" data-testid="lab-tree">
          <button type="button" className={`lab-tree-row lab-notes-entry ${notesOpen ? "on" : ""}`} onClick={showNotes} data-testid="lab-notes-open">
            <Icon name="notebook" /> <span>Note del laboratorio</span>
            {store.notes.trim() ? <span className="lab-count" title="Ci sono delle note">•</span> : null}
          </button>
          {files.length === 0 ? (
            <p className="muted small lab-tree-empty">Nessun file.</p>
          ) : (
            <FolderView folder={root} depth={0} open={open} choose={choose} canEdit={canEdit} counts={perFile} onRename={setRenaming} onRemove={setRemoving} />
          )}
        </aside>
        <main className="lab-main">
          {notesOpen ? (
            <NotesView text={store.notes} onChange={store.setNotes} canEdit={canEdit} />
          ) : open ? (
            <FileView key={`${open.id}:${reloads[open.id] ?? 0}`} file={open} base={base} store={store} canEdit={canEdit} />
          ) : (
            <div className="lab-empty">
              <Empty icon="flask">
                {canEdit ? (
                  <>
                    Trascina qui i file del laboratorio (anche intere cartelle) oppure{" "}
                    <button type="button" className="link" onClick={() => fileInput.current?.click()}>scegline qualcuno</button>. Fino a 20 MB l’uno; non vengono mai eseguiti.
                  </>
                ) : (
                  "Il laboratorio non ha ancora file."
                )}
              </Empty>
            </div>
          )}
        </main>
      </div>
      {dragging && <div className="lab-drop" aria-hidden><Icon name="upload" /> Lascia qui i file per caricarli</div>}

      {renaming && <RenameDialog file={renaming} onSave={(p) => rename(renaming, p)} onClose={() => setRenaming(null)} />}
      {removing && (
        <Confirm
          title="Eliminare il file?"
          danger
          confirmLabel="Elimina"
          message={<>«{removing.path}» esce dal laboratorio.</>}
          onConfirm={() => remove(removing)}
          onClose={() => setRemoving(null)}
        />
      )}
    </div>
  );
}

function FolderView({ folder, depth, open, choose, canEdit, counts, onRename, onRemove }: {
  folder: Folder; depth: number; open: LabFile | null; choose: (p: string) => void; canEdit: boolean; counts: Map<number, number>;
  onRename: (f: LabFile) => void; onRemove: (f: LabFile) => void;
}) {
  return (
    <ul className="lab-tree-list" role={depth === 0 ? "tree" : "group"}>
      {folder.folders.map((d) => (
        <li key={d.path} role="treeitem" aria-expanded>
          <details open>
            <summary className="lab-tree-row lab-folder" style={{ paddingLeft: `${0.5 + depth * 0.85}rem` }}>
              <Icon name="folder" /> {d.name}
            </summary>
            <FolderView folder={d} depth={depth + 1} open={open} choose={choose} canEdit={canEdit} counts={counts} onRename={onRename} onRemove={onRemove} />
          </details>
        </li>
      ))}
      {folder.files.map((f) => (
        <li key={f.id} role="treeitem" aria-selected={open?.id === f.id}>
          <div className={`lab-tree-row lab-file ${open?.id === f.id ? "on" : ""}`} style={{ paddingLeft: `${0.5 + depth * 0.85}rem` }}>
            <button type="button" className="lab-file-name" onClick={() => choose(f.path)} title={f.path} data-testid="lab-file">
              <Icon name={fileIcon(f)} /> <span>{baseName(f.path)}</span>
              {counts.get(f.id) ? <span className="lab-count" title={`${counts.get(f.id)} commenti`}>{counts.get(f.id)}</span> : null}
            </button>
            {canEdit && (
              <span className="lab-file-actions">
                <button type="button" className="btn ghost icon sm" onClick={() => onRename(f)} aria-label={`Rinomina ${f.path}`} title="Rinomina o sposta">
                  <Icon name="pencil" />
                </button>
                <button type="button" className="btn ghost icon sm" onClick={() => onRemove(f)} aria-label={`Elimina ${f.path}`} title="Elimina">
                  <Icon name="trash" />
                </button>
              </span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

function FileView({ file, base, store, canEdit }: { file: LabFile; base: string; store: Store; canEdit: boolean }) {
  const raw = `${base}/lab/files/${file.id}/raw`;
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [writing, setWriting] = useState<string | null>(null);
  const [reveal, setReveal] = useState<{ line: number } | null>(null);
  const [editing, setEditing] = useState(false);
  const isText = file.kind === "text" || file.kind === "notebook";
  const canChange = canEdit && file.kind === "text";
  useEffect(() => {
    if (!isText) return;
    api<{ content: string }>(`${base}/lab/files/${file.id}`)
      .then((r) => {
        // An edit made here and not saved yet (the tab was closed, the connection dropped) comes back.
        const pending = store.pendingContent(file.id);
        setContent(pending ?? r.content);
        if (pending !== undefined) setEditing(true);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, file.id, isText]);

  const mine = useMemo(() => sortComments(store.comments.filter((c) => c.file_id === file.id)), [store.comments, file.id]);
  const marks = useMemo<Mark[]>(
    () => mine.flatMap((c) => (isLines(c.anchor) && !c.anchor.gone ? [{ id: c.id, from: c.anchor.from, to: c.anchor.to }] : [])),
    [mine],
  );
  const mineRef = useRef(mine);
  mineRef.current = mine;
  const count = (of: (a: LabComment["anchor"]) => number | null) => {
    const m = new Map<number, number>();
    for (const c of mine) {
      const n = of(c.anchor);
      if (n !== null) m.set(n, (m.get(n) ?? 0) + 1);
    }
    return m;
  };
  const cellCounts = useMemo(() => count(cellOf), [mine]); // eslint-disable-line react-hooks/exhaustive-deps
  const pageCounts = useMemo(() => count(pageOf), [mine]); // eslint-disable-line react-hooks/exhaustive-deps
  const activeAnchor = mine.find((c) => c.id === active)?.anchor ?? {};
  const onMoved = useCallback(
    (moved: Moved[]) => {
      for (const m of moved) {
        const c = mineRef.current.find((x) => x.id === m.id);
        if (!c || !isLines(c.anchor)) continue;
        store.setAnchor(m.id, m.gone ? { ...c.anchor, gone: true } : { from: m.from, to: m.to, text: m.text });
      }
    },
    [store],
  );

  const start = (anchor: LabComment["anchor"]) => {
    const id = store.create(file.id, anchor);
    setActive(id);
    setWriting(id);
  };
  const focus = (id: string) => {
    setActive(id);
    document.getElementById(`lab-comment-${id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  return (
    <div className="lab-file-view">
      <div className="lab-file-head">
        <Icon name={fileIcon(file)} />
        <span className="mono lab-file-path" data-testid="lab-open-path">{file.path}</span>
        <span className="muted small">{[file.language && file.language !== "text" ? file.language : null, fmtSize(file.size)].filter(Boolean).join(" · ")}</span>
        <span className="grow" />
        {file.kind === "text" && canEdit && !editing && <span className="muted small hide-mobile">Seleziona delle righe per commentarle</span>}
        {editing && <span className="muted small hide-mobile">Modifiche salvate con i commenti (Ctrl+S) · Ctrl+Alt+M commenta</span>}
        {canChange && content !== null && (
          <button
            type="button"
            className={`btn sm ${editing ? "primary" : "ghost"}`}
            onClick={() => {
              if (editing) void store.flushAll();
              setEditing(!editing);
            }}
            data-testid="lab-edit"
            title={editing ? "Torna alla lettura" : "Modifica il testo del file (i commenti seguono le loro righe)"}
          >
            <Icon name={editing ? "check" : "pencil"} /> {editing ? "Fine" : "Modifica"}
          </button>
        )}
        <a className="btn ghost icon sm" href={raw} download aria-label="Scarica il file" title="Scarica il file">
          <Icon name="download" />
        </a>
      </div>
      <div className="lab-file-split">
        <div className="lab-file-body">
          {error ? (
            <div className="alert danger">{error}</div>
          ) : isText && content === null ? (
            <Loading />
          ) : file.kind === "notebook" && content !== null ? (
            <NotebookView content={content} language={file.language} counts={cellCounts} activeCell={cellOf(activeAnchor)} canComment={canEdit} onComment={(cell) => start({ cell })} />
          ) : file.kind === "pdf" ? (
            <PdfPages url={raw} counts={pageCounts} activePage={pageOf(activeAnchor)} canComment={canEdit} onComment={(page) => start({ page })} />
          ) : isText && content !== null ? (
            <CodeView
              content={content}
              filename={file.path}
              marks={marks}
              active={active}
              reveal={reveal}
              editable={editing}
              onMark={focus}
              onComment={canEdit ? (lines: Lines) => start(lines) : undefined}
              onChange={(doc) => store.setFileContent(file.id, doc)}
              onMoved={onMoved}
            />
          ) : file.kind === "image" ? (
            <div className="lab-image"><img src={raw} alt={file.path} /></div>
          ) : (
            <div className="lab-empty">
              <Empty icon={fileIcon(file)}>
                Questo file non è testo: si può solo scaricare (non viene mai eseguito).{" "}
                <a href={raw} download>Scarica {baseName(file.path)}</a>
              </Empty>
            </div>
          )}
        </div>
        <aside className="lab-comments" aria-label="Commenti del file" data-testid="lab-comments">
          <div className="lab-comments-head">
            <span className="section-label">Commenti</span>
            {canEdit && (
              <button type="button" className="btn ghost sm" onClick={() => start({})} data-testid="lab-comment-file" title="Un commento su tutto il file">
                <Icon name="plus" /> Sul file
              </button>
            )}
          </div>
          {mine.length === 0 ? (
            <p className="muted small lab-comments-empty">
              {!canEdit
                ? "Nessun commento."
                : file.kind === "text"
                  ? "Seleziona delle righe del codice e premi «Commenta» (Ctrl+Alt+M), oppure commenta tutto il file."
                  : file.kind === "notebook"
                    ? "Commenta una cella con il suo fumetto, oppure tutto il file."
                    : file.kind === "pdf"
                      ? "Commenta una pagina con «Commenta», oppure tutto il file."
                      : "Commenta tutto il file con «Sul file»."}
            </p>
          ) : (
            mine.map((c) => (
              <CommentCard
                key={c.id}
                comment={c}
                active={active === c.id}
                editing={writing === c.id}
                canEdit={canEdit}
                onPick={() => {
                  if (writing === c.id) return;
                  setActive(c.id);
                  if (isLines(c.anchor) && !c.anchor.gone) setReveal({ line: c.anchor.from });
                  const cell = cellOf(c.anchor);
                  const page = pageOf(c.anchor);
                  if (cell ?? page) document.getElementById(cell ? `lab-cell-${cell}` : `lab-page-${page}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
                }}
                onEdit={() => setWriting(c.id)}
                onDone={() => {
                  setWriting(null);
                  if (!c.body.trim()) store.remove(c.id);
                }}
                onBody={(b) => store.setBody(c.id, b)}
                onRemove={() => store.remove(c.id)}
              />
            ))
          )}
        </aside>
      </div>
    </div>
  );
}

function NotesView({ text, onChange, canEdit }: { text: string; onChange: (t: string) => void; canEdit: boolean }) {
  const [preview, setPreview] = useState(!canEdit);
  return (
    <div className="lab-file-view">
      <div className="lab-file-head">
        <Icon name="notebook" />
        <span className="lab-file-path">Note del laboratorio</span>
        <span className="muted small">non legate a un file: avvisi, consegne, cosa chiede all’esame</span>
        <span className="grow" />
        {canEdit && (
          <button type="button" className={`btn ghost icon sm ${preview ? "active" : ""}`} aria-pressed={preview} onClick={() => setPreview(!preview)} aria-label="Anteprima delle note" title="Anteprima delle note (Markdown)">
            <Icon name="eye" />
          </button>
        )}
      </div>
      <div className="lab-file-body lab-notes" data-testid="lab-notes">
        {preview ? (
          text.trim() ? <div className="lab-comment-text"><Markdown text={text} /></div> : <p className="muted">Nessuna nota.</p>
        ) : (
          <NotesField value={text} onChange={onChange} minHeight={320} label="Note del laboratorio" placeholder="Le note del laboratorio, in Markdown: elenchi con -, **grassetto**, `codice`, formule con $…$" />
        )}
      </div>
    </div>
  );
}

function sortComments(list: LabComment[]): LabComment[] {
  // Whole-file comments first, then by line; those whose lines were removed at the end.
  const line = (c: LabComment) => (isLines(c.anchor) ? (c.anchor.gone ? Number.MAX_SAFE_INTEGER : c.anchor.from) : (cellOf(c.anchor) ?? pageOf(c.anchor) ?? 0));
  return [...list].sort((a, b) => line(a) - line(b) || a.created_at.localeCompare(b.created_at));
}

function where(c: LabComment): string {
  const cell = cellOf(c.anchor);
  const page = pageOf(c.anchor);
  if (cell) return `Cella ${cell}`;
  if (page) return `Pagina ${page}`;
  if (!isLines(c.anchor)) return "Tutto il file";
  if (c.anchor.gone) return "Righe rimosse";
  return c.anchor.from === c.anchor.to ? `Riga ${c.anchor.from}` : `Righe ${c.anchor.from}–${c.anchor.to}`;
}

function CommentCard({ comment, active, editing, canEdit, onPick, onEdit, onDone, onBody, onRemove }: {
  comment: LabComment; active: boolean; editing: boolean; canEdit: boolean;
  onPick: () => void; onEdit: () => void; onDone: () => void; onBody: (b: string) => void; onRemove: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (editing) box.current?.querySelector("textarea")?.focus();
  }, [editing]);
  const snippet = isLines(comment.anchor) ? comment.anchor.text.split("\n").find((l) => l.trim())?.trim() : null;
  return (
    <article id={`lab-comment-${comment.id}`} className={`lab-comment ${active ? "on" : ""} ${isLines(comment.anchor) && comment.anchor.gone ? "gone" : ""}`} data-testid="lab-comment" onClick={onPick}>
      <header className="lab-comment-head">
        <span className="lab-comment-where">{where(comment)}</span>
        {snippet && <code className="lab-comment-snippet">{snippet}</code>}
        <span className="grow" />
        {canEdit && (
          <button type="button" className="btn ghost icon sm" onClick={(e) => { e.stopPropagation(); onRemove(); }} aria-label="Elimina il commento" title="Elimina il commento">
            <Icon name="trash" />
          </button>
        )}
      </header>
      <div
        ref={box}
        className="lab-comment-body"
        onBlur={(e) => {
          if (editing && !e.currentTarget.contains(e.relatedTarget as Node | null)) onDone();
        }}
      >
        {editing ? (
          <NotesField value={comment.body} onChange={onBody} minHeight={72} label="Commento" placeholder="Il commento, in Markdown: elenchi con -, **grassetto**, `codice`, formule con $…$" />
        ) : comment.body.trim() ? (
          <div onClick={canEdit ? onEdit : undefined} className={canEdit ? "lab-comment-text editable" : "lab-comment-text"} title={canEdit ? "Clicca per modificare" : undefined}>
            <Markdown text={comment.body} />
          </div>
        ) : (
          canEdit && <button type="button" className="link muted small" onClick={onEdit}>Scrivi il commento…</button>
        )}
      </div>
    </article>
  );
}

function RenameDialog({ file, onSave, onClose }: { file: LabFile; onSave: (path: string) => void; onClose: () => void }) {
  const [path, setPath] = useState(file.path);
  return (
    <Modal
      title="Rinomina il file"
      onClose={onClose}
      actions={
        <>
          <button type="button" className="btn" onClick={onClose}>Annulla</button>
          <button type="button" className="btn primary" onClick={() => path.trim() && onSave(path.trim())} disabled={!path.trim()} data-testid="lab-rename-save">
            <Icon name="check" /> Rinomina
          </button>
        </>
      }
    >
      <label className="field">
        Nome, con le cartelle («src/main.c»)
        <input type="text" value={path} onChange={(e) => setPath(e.target.value)} autoFocus data-testid="lab-rename-input" />
      </label>
    </Modal>
  );
}
