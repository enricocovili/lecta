import { useEffect, useRef, useState } from "react";
import { ApiError, fmtSize, get, post, uploadRaw } from "../lib/api";
import { Icon } from "./icons";
import type { TreeCourse } from "./types";
import GuidelinesField from "./lessons/GuidelinesField";
import { Confirm, Modal, Progress, toastError } from "./ui";

interface Entry {
  file: File;
  path: string;
  progress: number;
  status: "queued" | "uploading" | "done" | "skipped" | "error";
  message?: string;
  /** Object URL for image thumbnails. */
  preview?: string;
}

const ENTRY_STATUS: Record<Entry["status"], { label: string; cls: string }> = {
  queued: { label: "in attesa", cls: "" },
  uploading: { label: "caricamento", cls: "warn" },
  done: { label: "caricato", cls: "ok" },
  skipped: { label: "saltato", cls: "warn" },
  error: { label: "errore", cls: "danger" },
};

/** Collect files from a drop event, including folders (webkitGetAsEntry). */
async function filesFromDrop(dt: DataTransfer): Promise<{ file: File; path: string }[]> {
  const out: { file: File; path: string }[] = [];
  const items = Array.from(dt.items || []);
  const entries = items.map((i) => (i as DataTransferItem & { webkitGetAsEntry?: () => FileSystemEntry | null }).webkitGetAsEntry?.()).filter(Boolean) as FileSystemEntry[];
  if (entries.length === 0) {
    for (const f of Array.from(dt.files)) out.push({ file: f, path: f.name });
    return out;
  }
  const walk = async (entry: FileSystemEntry, prefix: string): Promise<void> => {
    if (entry.isFile) {
      const file = await new Promise<File>((res, rej) => (entry as FileSystemFileEntry).file(res, rej));
      out.push({ file, path: prefix + file.name });
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      let batch: FileSystemEntry[] = [];
      do {
        batch = await new Promise<FileSystemEntry[]>((res, rej) => reader.readEntries(res, rej));
        for (const e of batch) await walk(e, prefix + entry.name + "/");
      } while (batch.length > 0);
    }
  };
  for (const e of entries) await walk(e, "");
  return out;
}

/** Class notes: the bullet points taken during the lecture, the backbone of the text. */
const NOTES_RE = /\.(md|markdown|mdown|txt)$/i;

function isNoNotes(e: unknown): boolean {
  if (!(e instanceof ApiError) || e.status !== 409) return false;
  const d = (e.detail as { detail?: { code?: string } } | null)?.detail;
  return d?.code === "no_notes";
}

function fileIcon(f: File): string {
  if (f.type.startsWith("image/")) return "image";
  if (/\.zip$/i.test(f.name)) return "archive";
  if (f.type === "application/pdf" || /\.pdf$/i.test(f.name)) return "file-text";
  return "file";
}

export default function Upload({ quick = false }: { quick?: boolean }) {
  const [tree, setTree] = useState<TreeCourse[]>([]);
  const [courseId, setCourseId] = useState<string>("");
  const [chapterId, setChapterId] = useState<string>("");
  const [note, setNote] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  // Set once the upload exists: after the notes warning, more files can be added to the same upload.
  const [uploadId, setUploadId] = useState<number | null>(null);
  const [askNotes, setAskNotes] = useState(false);
  // The subject's writing guidelines, asked before the import starts when a subject is chosen (null = not asked yet).
  const [guide, setGuide] = useState<{ text: string; save: boolean } | null>(null);
  const [askGuide, setAskGuide] = useState<{ withoutNotes: boolean } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const dirInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    get<TreeCourse[]>("/api/tree").then(setTree).catch(() => undefined);
    const p = new URLSearchParams(location.search);
    if (p.get("course")) setCourseId(p.get("course")!);
    if (p.get("chapter")) setChapterId(p.get("chapter")!);
  }, []);

  const add = (files: { file: File; path: string }[]) =>
    setEntries((cur) => [
      ...cur,
      ...files.map((f) => ({
        file: f.file,
        path: f.path,
        progress: 0,
        status: "queued" as const,
        preview: f.file.type.startsWith("image/") ? URL.createObjectURL(f.file) : undefined,
      })),
    ]);

  const remove = (i: number) =>
    setEntries((cur) => {
      if (cur[i]?.preview) URL.revokeObjectURL(cur[i].preview!);
      return cur.filter((_, j) => j !== i);
    });

  const hasNotes = entries.some((e) => NOTES_RE.test(e.path));
  const hasZip = entries.some((e) => /\.zip$/i.test(e.path));
  // Zips are checked by the server once uploaded.
  const notesMissing = !quick && entries.length > 0 && !hasNotes && !hasZip;

  const start = async (withoutNotes = false, g: { text: string; save: boolean } | null = guide) => {
    if (entries.length === 0) return;
    if (notesMissing && !withoutNotes) {
      setAskNotes(true);
      return;
    }
    if (courseId && !quick && g === null) {
      setAskGuide({ withoutNotes });
      return;
    }
    setBusy(true);
    try {
      let id = uploadId;
      if (id === null) {
        const up = await post<{ id: number }>("/api/uploads", {
          course_id: courseId ? Number(courseId) : null,
          chapter_id: chapterId ? Number(chapterId) : null,
          note: note || null,
          via: quick ? "quick" : "web",
        });
        id = up.id;
        setUploadId(id);
      }
      for (let i = 0; i < entries.length; i++) {
        if (entries[i].status !== "queued") continue;
        setEntries((cur) => cur.map((e, j) => (j === i ? { ...e, status: "uploading" } : e)));
        try {
          const r = await uploadRaw<{ skipped?: boolean; status?: string; reason?: string }>(
            `/api/uploads/${id}/files?name=${encodeURIComponent(entries[i].path)}`,
            entries[i].file,
            (f) => setEntries((cur) => cur.map((e, j) => (j === i ? { ...e, progress: f } : e))),
          );
          setEntries((cur) =>
            cur.map((e, j) =>
              j === i
                ? {
                    ...e,
                    progress: 1,
                    status: r.skipped ? "skipped" : r.status === "unsupported" ? "skipped" : "done",
                    message: r.reason ?? (r.status === "unsupported" ? "tipo di file non supportato" : undefined),
                  }
                : e,
            ),
          );
        } catch (err) {
          setEntries((cur) => cur.map((e, j) => (j === i ? { ...e, status: "error", message: err instanceof Error ? err.message : String(err) } : e)));
        }
      }
      try {
        const fin = await post<{ job_id: number }>(`/api/uploads/${id}/finish`, {
          without_notes: withoutNotes,
          ...(g ? { guidelines: g.text.trim(), save_guidelines: g.save } : {}),
        });
        location.href = `/admin/jobs/${fin.job_id}`;
      } catch (e) {
        if (!isNoNotes(e)) throw e;
        setBusy(false);
        setAskNotes(true);
      }
    } catch (e) {
      toastError(e);
      setBusy(false);
    }
  };

  const course = tree.find((c) => String(c.id) === courseId);
  const total = entries.reduce((a, e) => a + e.file.size, 0);
  const done = entries.filter((e) => e.status !== "queued" && e.status !== "uploading").length;

  const destination = (
    <div className={quick ? "stack" : "form-grid"}>
      <label className="field">
        Materia <span className="hint">facoltativa</span>
        <select
          value={courseId}
          disabled={uploadId !== null}
          onChange={(e) => {
            setCourseId(e.target.value);
            setChapterId("");
          }}
        >
          <option value="">Lascia che Lecta la smisti</option>
          {tree.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Capitolo <span className="hint">facoltativo: unisci a questo capitolo</span>
        <select value={chapterId} onChange={(e) => setChapterId(e.target.value)} disabled={!course || uploadId !== null}>
          <option value="">{course ? "Decide Lecta (esistente o nuovo)" : "Scegli prima una materia"}</option>
          {(course?.chapters ?? []).map((ch) => (
            <option key={ch.id} value={ch.id}>
              {String(ch.position).padStart(2, "0")} {ch.title}
            </option>
          ))}
        </select>
      </label>
      {!quick && (
        <label className="field pg-note-field">
          Nota <span className="hint">solo per te, non viene inviata a nessuno</span>
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Es. lezione del 12 ottobre" />
        </label>
      )}
    </div>
  );

  return (
    <div className={quick ? "pg-quick stack" : "pg-upload"}>
      {quick ? (
        <div className="pg-quick-actions">
          <button className="btn primary pg-quick-btn" onClick={() => cameraInput.current?.click()} disabled={busy}>
            <Icon name="camera" />
            Scatta foto
          </button>
          <button className="btn pg-quick-btn secondary" onClick={() => fileInput.current?.click()} disabled={busy}>
            <Icon name="file" />
            Scegli file
          </button>
        </div>
      ) : (
        <div
          className={`dropzone pg-dropzone ${over ? "over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={async (e) => {
            e.preventDefault();
            setOver(false);
            add(await filesFromDrop(e.dataTransfer));
          }}
          onClick={() => fileInput.current?.click()}
        >
          <span className="pg-drop-ico">
            <Icon name="upload" />
          </span>
          <div className="pg-drop-title">Trascina qui file o cartelle</div>
          <div className="pg-drop-sub">
            I tuoi appunti della lezione (.md o .txt) con le slide in PDF, e se vuoi foto (JPEG, PNG, HEIC) o uno zip con cartelle, una per lezione. Gli
            appunti sono la traccia del testo, le slide lo completano.
          </div>
          <div className="row pg-drop-actions" onClick={(e) => e.stopPropagation()}>
            <button className="btn" onClick={() => fileInput.current?.click()}>
              <Icon name="file" />
              Scegli file
            </button>
            <button className="btn" onClick={() => dirInput.current?.click()}>
              <Icon name="folder" />
              Scegli una cartella
            </button>
          </div>
        </div>
      )}
      <input
        ref={fileInput}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          add(Array.from(e.target.files ?? []).map((f) => ({ file: f, path: f.name })));
          e.target.value = "";
        }}
      />
      <input
        ref={dirInput}
        type="file"
        multiple
        hidden
        {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
        onChange={(e) => {
          add(Array.from(e.target.files ?? []).map((f) => ({ file: f, path: (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name })));
          e.target.value = "";
        }}
      />
      <input
        ref={cameraInput}
        type="file"
        accept="image/*"
        capture="environment"
        multiple
        hidden
        onChange={(e) => {
          add(Array.from(e.target.files ?? []).map((f, i) => ({ file: f, path: f.name || `photo-${Date.now()}-${i}.jpg` })));
          e.target.value = "";
        }}
      />

      <div className="section-label">Destinazione</div>
      <div className="card pg-pad">{destination}</div>

      {entries.length > 0 && (
        <>
          <div className="section-label">
            <span>
              File <span className="pg-tab-n">{entries.length}</span>
            </span>
            <span className="pg-upload-total mono">
              {busy ? `${done}/${entries.length} · ` : ""}
              {fmtSize(total)}
            </span>
          </div>
          <div className="card flush">
            <div className="rows">
              {entries.map((e, i) => {
                const st = ENTRY_STATUS[e.status];
                return (
                  <div key={i} className="pg-row pg-file-row">
                    {e.preview ? (
                      <img className="pg-file-thumb" src={e.preview} alt="" />
                    ) : (
                      <span className="status-ico pg-kind-ico">
                        <Icon name={fileIcon(e.file)} />
                      </span>
                    )}
                    <div className="grow">
                      <div className="row between pg-nw" style={{ gap: ".75rem" }}>
                        <span className="pg-file-name ellipsis">{e.path}</span>
                        <span className="pg-time mono">{fmtSize(e.file.size)}</span>
                      </div>
                      {e.status !== "queued" && <Progress value={e.progress} tone={e.status === "error" ? "danger" : e.status === "done" ? "ok" : "warn"} />}
                      {e.message && <div className="pg-row-sub">{e.message}</div>}
                    </div>
                    <span className={`badge ${st.cls}`}>{st.label}</span>
                    {!busy && e.status === "queued" && (
                      <button className="btn ghost icon sm" onClick={() => remove(i)} aria-label={`Togli ${e.path}`} title="Togli">
                        <Icon name="x" />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
          {notesMissing && (
            <div className="alert warn small pg-notes-missing">
              <Icon name="alert-triangle" />
              <span className="grow">
                Manca il file con gli appunti della lezione (<span className="mono">.md</span> o <span className="mono">.txt</span>): è la traccia del
                testo. Senza, Lecta riassume le sole slide.
              </span>
              {!busy && (
                <button type="button" className="btn sm" onClick={() => fileInput.current?.click()}>
                  Aggiungi appunti
                </button>
              )}
            </div>
          )}
          <div className="pg-upload-go">
            <button className={`btn primary ${quick ? "lg pg-quick-submit" : "lg"}`} disabled={busy} onClick={() => start()}>
              {busy ? <Icon name="loader" className="spin" /> : <Icon name="upload" />}
              {busy ? "Caricamento…" : "Carica ed elabora"}
            </button>
            {!busy && (
              <button
                className="btn ghost"
                onClick={() => {
                  entries.forEach((e) => e.preview && URL.revokeObjectURL(e.preview));
                  setEntries([]);
                  setUploadId(null);
                }}
              >
                Svuota
              </button>
            )}
          </div>
          <p className="pg-upload-note">
            <Icon name="sparkles" />
            L’importazione va avanti da sola: segue i tuoi appunti, li completa con le slide e ne fa un testo discorsivo da studiare, nella materia scelta (o
            in quella più adatta). In «Da smistare» finisce solo quello che non sa dove mettere.
          </p>
        </>
      )}
      {askGuide && course && (
        <UploadGuidelines
          courseId={course.id}
          courseName={course.name}
          onCancel={() => setAskGuide(null)}
          onConfirm={(g) => {
            const w = askGuide.withoutNotes;
            setGuide(g);
            setAskGuide(null);
            void start(w, g);
          }}
        />
      )}
      {askNotes && (
        <Confirm
          title="Mancano gli appunti della lezione"
          message={
            <>
              Non c’è un file <strong>.md</strong> o <strong>.txt</strong> con gli appunti presi a lezione. Sono la traccia del testo: Lecta segue i loro
              punti e li completa con le slide. Senza, il testo sarà un riassunto delle sole slide.
            </>
          }
          cancelLabel="Aggiungi gli appunti"
          confirmLabel="Continua senza appunti"
          onConfirm={() => start(true)}
          onCancel={() => fileInput.current?.click()}
          onClose={() => setAskNotes(false)}
        />
      )}
    </div>
  );
}

/** «Linee guida» before an import into a chosen subject: the saved ones, or the notice that the text will be automatic. */
function UploadGuidelines({ courseId, courseName, onConfirm, onCancel }: { courseId: number; courseName: string; onConfirm: (g: { text: string; save: boolean }) => void; onCancel: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [save, setSave] = useState(true);
  useEffect(() => {
    get<{ guidelines?: string }>(`/api/courses/${courseId}`)
      .then((c) => setText(c.guidelines ?? ""))
      .catch(() => setText(""));
  }, [courseId]);
  return (
    <Modal
      title="Linee guida per la scrittura"
      onClose={onCancel}
      wide
      actions={
        <>
          <button className="btn" onClick={onCancel}>
            Annulla
          </button>
          <button className="btn primary" disabled={text === null} onClick={() => onConfirm({ text: text ?? "", save })} data-testid="guidelines-go">
            {(text ?? "").trim() ? "Continua con queste linee guida" : "Continua in automatico"}
          </button>
        </>
      }
    >
      <div className="stack">
        <GuidelinesField value={text ?? ""} onChange={setText} subject={courseName} />
        <label className="check small">
          <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} />
          Ricorda queste linee guida per la materia
        </label>
      </div>
    </Modal>
  );
}
