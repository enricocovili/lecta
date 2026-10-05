// The laboratory of a lesson: the files explained in class, in a tree on the left and open in the middle (code with its
// colours, pictures; PDFs and other files to download). Files come in from a picker, a folder or a drop, several at once.
// Nothing is ever run: text is shown as text.
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { ApiError, api, del, fmtSize, get, patch, post, uploadRaw } from "../../lib/api";
import { Icon } from "../icons";
import { Confirm, Empty, Loading, Modal, toast, toastError } from "../ui";
import CodeView from "./CodeView";
import { MAX_FILE_BYTES, baseName, fileIcon, fromDrop, fromInput, tree, worthUploading, type Folder, type LabAccess, type LabData, type LabFile, type Picked } from "./files";

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

/** Which file is open: kept in the address (`?file=src/main.c`) so a link or a reload opens it again. */
function useOpenFile(files: LabFile[]) {
  const [path, setPath] = useState<string | null>(() => new URLSearchParams(location.search).get("file"));
  const open = files.find((f) => f.path === path) ?? null;
  const choose = useCallback((p: string | null) => {
    setPath(p);
    const url = new URL(location.href);
    if (p) url.searchParams.set("file", p);
    else url.searchParams.delete("file");
    history.replaceState(null, "", url);
  }, []);
  return [open, choose] as const;
}

export function LabEditor({ lab, access, base, lessonHref }: { lab: LabData; access: LabAccess; base: string; lessonHref: string | null }) {
  const owner = access === "owner";
  const canEdit = access !== "read";
  const [files, setFiles] = useState<LabFile[]>(lab.files);
  const [open, choose] = useOpenFile(files);
  const [uploading, setUploading] = useState<{ name: string; done: number; total: number; fraction: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [renaming, setRenaming] = useState<LabFile | null>(null);
  const [removing, setRemoving] = useState<LabFile | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const root = useMemo(() => tree(files), [files]);

  // The first file opens by itself when none is chosen.
  useEffect(() => {
    if (!open && files.length) choose(files[0].path);
  }, [open, files, choose]);

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
          if (replaced) toast(`«${file.path}» sostituito con il nuovo file`);
          last = file.path;
        } catch (e) {
          toastError(e);
        }
      }
      setUploading(null);
      if (last) choose(last);
    },
    [base, choose],
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
          {files.length === 0 ? (
            <p className="muted small lab-tree-empty">Nessun file.</p>
          ) : (
            <FolderView folder={root} depth={0} open={open} choose={choose} canEdit={canEdit} onRename={setRenaming} onRemove={setRemoving} />
          )}
        </aside>
        <main className="lab-main">
          {open ? (
            <FileView key={`${open.id}:${open.version}`} file={open} base={base} />
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

function FolderView({ folder, depth, open, choose, canEdit, onRename, onRemove }: {
  folder: Folder; depth: number; open: LabFile | null; choose: (p: string) => void; canEdit: boolean;
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
            <FolderView folder={d} depth={depth + 1} open={open} choose={choose} canEdit={canEdit} onRename={onRename} onRemove={onRemove} />
          </details>
        </li>
      ))}
      {folder.files.map((f) => (
        <li key={f.id} role="treeitem" aria-selected={open?.id === f.id}>
          <div className={`lab-tree-row lab-file ${open?.id === f.id ? "on" : ""}`} style={{ paddingLeft: `${0.5 + depth * 0.85}rem` }}>
            <button type="button" className="lab-file-name" onClick={() => choose(f.path)} title={f.path} data-testid="lab-file">
              <Icon name={fileIcon(f)} /> <span>{baseName(f.path)}</span>
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

function FileView({ file, base }: { file: LabFile; base: string }) {
  const raw = `${base}/lab/files/${file.id}/raw`;
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isText = file.kind === "text" || file.kind === "notebook";
  useEffect(() => {
    if (!isText) return;
    api<{ content: string }>(`${base}/lab/files/${file.id}`)
      .then((r) => setContent(r.content))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [base, file.id, isText]);

  return (
    <div className="lab-file-view">
      <div className="lab-file-head">
        <Icon name={fileIcon(file)} />
        <span className="mono lab-file-path" data-testid="lab-open-path">{file.path}</span>
        <span className="muted small">{[file.language && file.language !== "text" ? file.language : null, fmtSize(file.size)].filter(Boolean).join(" · ")}</span>
        <span className="grow" />
        <a className="btn ghost icon sm" href={raw} download aria-label="Scarica il file" title="Scarica il file">
          <Icon name="download" />
        </a>
      </div>
      <div className="lab-file-body">
        {error ? (
          <div className="alert danger">{error}</div>
        ) : isText ? (
          content === null ? <Loading /> : <CodeView content={content} filename={file.path} />
        ) : file.kind === "image" ? (
          <div className="lab-image"><img src={raw} alt={file.path} /></div>
        ) : (
          <div className="lab-empty">
            <Empty icon={fileIcon(file)}>
              {file.kind === "pdf" ? "I PDF si leggono qui a breve: per ora scaricalo." : "Questo file non è testo: si può solo scaricare (non viene mai eseguito)."}{" "}
              <a href={raw} download>Scarica {baseName(file.path)}</a>
            </Empty>
          </div>
        )}
      </div>
    </div>
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
