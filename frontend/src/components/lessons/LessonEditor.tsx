// The lesson workspace, for taking notes during the lecture: every slide with room to draw on it (pen, highlighter, eraser;
// mouse, stylus or finger) and Markdown notes next to it, saved as you write. Made to work on a tablet or a foldable laptop.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fmtDay, get, patch } from "../../lib/api";
import { courseText } from "../../lib/links";
import { Icon } from "../icons";
import Pop from "../workspace/Pop";
import { Confirm, Seg, toastError, useLocalStorage } from "../ui";
import GenerateDialog from "./GenerateDialog";
import { clampZoom, stepZoom, useGestures, ZOOM_MAX, ZOOM_MIN, type GestureOptions } from "./gestures";
import ShareDialog from "./ShareDialog";
import { COLORS, HL_COLORS, HL_WIDTHS, PEN_WIDTHS, type Stroke, type Tool } from "./ink";
import LessonStatusPill, { type LessonStatus } from "./LessonStatus";
import PageRow, { type Actions, type DrawSettings } from "./PageRow";
import { usePdfDoc } from "./SlideView";
import { useLessonPages, type LessonSource, type PageState, type SaveState } from "./useLesson";

export interface LessonData {
  id: number;
  number: number;
  course_id: number;
  course_name: string;
  course_guidelines: string;
  title: string;
  status: LessonStatus;
  has_pdf: boolean;
  pdf_pages: number;
  generated_at: string | null;
  chapter_id: number | null;
  last_page_id: number | null;
  last_result: { chapters?: { chapter_id: number | null; title: string | null }[] } | null;
  /** the lesson's laboratory, if it has one */
  lab?: { files: number } | null;
  pages: PageState[];
}

/** owner: the signed-in author; write: a share link that can edit; read: a share link that only looks. */
export type Access = "owner" | "write" | "read";

const SAVE_LABEL: Record<SaveState, string> = { saved: "Salvato", pending: "Da salvare", saving: "Salvataggio…", offline: "Non salvato · riprovo" };

export default function LessonEditor({ courseId, number }: { courseId: number; number: number }) {
  const [data, setData] = useState<LessonData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    get<LessonData>(`/api/courses/${courseId}/lessons/${number}`)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [courseId, number]);
  if (error) {
    return (
      <div className="les les-loading">
        <div className="alert danger">{error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="les les-loading muted">
        <Icon name="loader" className="spin" /> Caricamento…
      </div>
    );
  }
  return <Editor lesson={data} access="owner" source={{ base: `/api/lessons/${data.id}`, key: String(data.id) }} />;
}

/** Brings a page (the slide and its notes) to the middle of the editor; one taller than the editor starts at the top instead. */
function reveal(pageId: number, smooth = false) {
  const el = document.getElementById(`lesson-page-${pageId}`);
  if (!el) return;
  const room = el.closest(".les-scroll")?.clientHeight ?? window.innerHeight;
  el.scrollIntoView({ block: el.getBoundingClientRect().height < room ? "center" : "start", behavior: smooth ? "smooth" : "auto" });
}

function pageLabel(pages: PageState[], index: number): string {
  const p = pages[index];
  if (p.kind === "slide") return `Slide ${p.slide_page}`;
  const after = [...pages.slice(0, index)].reverse().find((x) => x.kind === "slide")?.slide_page;
  return after ? `Pagina aggiunta dopo la slide ${after}` : "Pagina aggiunta";
}

export function Editor({ lesson, access, source, labHref }: { lesson: LessonData; access: Access; source: LessonSource; labHref?: string | null }) {
  const owner = access === "owner";
  const readOnly = access === "read";
  const [title, setTitle] = useState(lesson.title);
  const [hasShares, setHasShares] = useState(false);
  // Others save too when the lesson is shared (and a visitor sees what the owner and the others write): their pages are brought in.
  const onTitle = useCallback(
    (t: string) => {
      if (owner) return;
      setTitle(t);
      document.title = `${t} · Lecta`;
    },
    [owner],
  );
  const store = useLessonPages(source, lesson.pages, readOnly ? 5000 : access === "write" ? 6000 : hasShares ? 8000 : null, onTitle);
  const { pages } = store;
  const scroller = useRef<HTMLDivElement>(null);
  const { doc, error: pdfError } = usePdfDoc(lesson.has_pdf ? `${source.base}/pdf` : null);
  const [tool, setTool] = useState<Tool>("pen");
  const [colorIdx, setColorIdx] = useState(0);
  const [hlIdx, setHlIdx] = useState(0);
  const [penSize, setPenSize] = useLocalStorage("lecta:lesson:pen", 1);
  const [hlSize, setHlSize] = useLocalStorage("lecta:lesson:hl", 0);
  const [fingerDraws, setFingerDraws] = useLocalStorage("lecta:lesson:finger", false);
  const [shapes, setShapes] = useLocalStorage("lecta:lesson:shapes", true);
  const [layout, setLayout] = useLocalStorage<"side" | "stack" | "slides">("lecta:lesson:layout", "side");
  const [zoomSaved, setZoom] = useLocalStorage("lecta:lesson:scale", 1);
  const zoom = clampZoom(Number(zoomSaved) || 1);
  const [awake, setAwake] = useLocalStorage("lecta:lesson:awake", true);
  const [preview, setPreview] = useState(false);
  const [current, setCurrent] = useState(0);
  const [showGenerate, setShowGenerate] = useState(false);
  const [showShare, setShowShare] = useState(false);
  const [askRemove, setAskRemove] = useState<number | null>(null);
  // What the select tool picked: strokes of one page.
  const [selection, setSelection] = useState<{ pid: number; strokes: Stroke[] } | null>(null);

  useEffect(() => {
    if (owner) get<unknown[]>(`/api/lessons/${lesson.id}/shares`).then((l) => setHasShares(l.length > 0)).catch(() => undefined);
  }, [owner, lesson.id]);

  // Opening the lesson lands on the page that was open last, or the first one (the rows take their height while they measure
  // themselves, so the scroll is repeated a few times unless the user already moved).
  const restored = useRef(false);
  useEffect(() => {
    const target = pagesRef.current.find((p) => p.id === lesson.last_page_id) ?? pagesRef.current[0];
    if (!target) {
      restored.current = true;
      return;
    }
    let moved = false;
    const stop = () => {
      moved = true;
    };
    const root = scroller.current;
    root?.addEventListener("wheel", stop, { passive: true });
    root?.addEventListener("pointerdown", stop, { passive: true, capture: true }); // the gestures keep a finger's to themselves
    const go = () => !moved && reveal(target.id);
    const timers = [0, 120, 400, 1000].map((ms) => window.setTimeout(go, ms));
    const done = window.setTimeout(() => {
      restored.current = true;
    }, 1100);
    return () => {
      timers.forEach(clearTimeout);
      clearTimeout(done);
      root?.removeEventListener("wheel", stop);
      root?.removeEventListener("pointerdown", stop, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The page being looked at is remembered (a moment after it settles), also when the tab is closed.
  const lastSent = useRef<number | null>(lesson.last_page_id);
  const currentId = pages[current]?.id ?? null;
  const sendBookmark = useCallback(() => {
    if (!owner || !restored.current || currentId === null || currentId === lastSent.current) return;
    lastSent.current = currentId;
    api(`/api/lessons/${lesson.id}/last-page`, { method: "PUT", json: { page_id: currentId }, keepalive: true }).catch(() => {
      lastSent.current = null; // try again at the next change
    });
  }, [owner, currentId, lesson.id]);
  useEffect(() => {
    const t = window.setTimeout(sendBookmark, 1500);
    return () => clearTimeout(t);
  }, [sendBookmark]);
  useEffect(() => {
    const hide = () => document.visibilityState === "hidden" && sendBookmark();
    document.addEventListener("visibilitychange", hide);
    window.addEventListener("pagehide", sendBookmark);
    return () => {
      document.removeEventListener("visibilitychange", hide);
      window.removeEventListener("pagehide", sendBookmark);
    };
  }, [sendBookmark]);

  const draw = useMemo<DrawSettings>(
    () => ({ tool: readOnly ? "hand" : tool, color: COLORS[colorIdx], hlColor: HL_COLORS[hlIdx], penWidth: PEN_WIDTHS[Math.min(penSize, PEN_WIDTHS.length - 1)], hlWidth: HL_WIDTHS[Math.min(hlSize, HL_WIDTHS.length - 1)], fingerDraws, shapes }),
    [tool, readOnly, colorIdx, hlIdx, penSize, hlSize, fingerDraws, shapes],
  );
  const gestureOpts = useRef<GestureOptions>({ tool: draw.tool, fingerDraws, zoom, setZoom });
  gestureOpts.current = { tool: draw.tool, fingerDraws, zoom, setZoom };
  const zoomTo = useGestures(scroller, gestureOpts);

  const { setNotes, addStroke, eraseStrokes, moveStrokes, addBlankAfter, removePage, undo, redo, flushAll } = store;
  const save = useCallback(async () => {
    if (!(await flushAll())) toastError(new Error("Non riesco a salvare: riprovo appena torna la connessione"));
  }, [flushAll]);
  const pagesRef = useRef(pages);
  pagesRef.current = pages;
  const goTo = useCallback((index: number) => {
    const p = pagesRef.current[index];
    if (p) reveal(p.id, true);
  }, []);

  const actions = useMemo<Actions>(
    () => ({
      setNotes,
      addStroke,
      eraseStrokes,
      moveStrokes: (pid, moves) => {
        moveStrokes(pid, moves);
        setSelection({ pid, strokes: moves.map((m) => m.to) });
      },
      select: (pid, strokes) => setSelection(strokes.length ? { pid, strokes } : null),
      visible: setCurrent,
      addBlankAfter: (pid) => {
        addBlankAfter(pid)
          .then((id) => setTimeout(() => reveal(id, true), 80))
          .catch(toastError);
      },
      removePage: setAskRemove,
    }),
    [setNotes, addStroke, eraseStrokes, moveStrokes, addBlankAfter],
  );

  // The selection is let go when another tool is picked, and keeps only the strokes that are still there (undo, erasing,
  // someone else's save).
  useEffect(() => {
    if (tool !== "select") setSelection(null);
  }, [tool]);
  useEffect(() => {
    if (!selection) return;
    const ink = pages.find((p) => p.id === selection.pid)?.ink ?? [];
    const still = selection.strokes.filter((s) => ink.includes(s));
    if (still.length !== selection.strokes.length) setSelection(still.length ? { pid: selection.pid, strokes: still } : null);
  }, [pages, selection]);
  const deleteSelection = useCallback(() => {
    if (!selection) return;
    const ink = pagesRef.current.find((p) => p.id === selection.pid)?.ink ?? [];
    eraseStrokes(selection.pid, selection.strokes.map((s) => ink.indexOf(s)).filter((i) => i >= 0));
    setSelection(null);
  }, [selection, eraseStrokes]);

  // Keyboard: ← / → go to the previous / next page (also in the read-only share view; not while typing or in a dialog).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key !== "ArrowLeft" && e.key !== "ArrowRight") || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (document.querySelector(".modal-backdrop")) return;
      const next = e.key === "ArrowRight" ? current + 1 : current - 1;
      if (next < 0 || next >= pagesRef.current.length) return;
      e.preventDefault();
      goTo(next);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, goTo]);

  // Keyboard: undo/redo (also in a note: one history) and tool letters (not while typing).
  useEffect(() => {
    if (readOnly) return;
    const onKey = (e: KeyboardEvent) => {
      // Ctrl+S saves the lesson everywhere, also while typing, instead of the browser's "save page".
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
        return;
      }
      const t = e.target as HTMLElement | null;
      const mod = e.ctrlKey || e.metaKey;
      // Undo and redo are the lesson's own, one history for strokes, text and removed pages, also while typing in a note
      // (the text field's own undo would only know about the text). Other fields keep theirs.
      const inNotes = !!t?.classList?.contains("les-notes-field");
      const inField = !!t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.tagName === "SELECT" || t.isContentEditable);
      if (inField && !(inNotes && mod)) return;
      if (document.querySelector(".modal-backdrop")) return;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        redo();
      } else if ((e.key === "Delete" || e.key === "Backspace") && selection) {
        e.preventDefault();
        deleteSelection();
      } else if (e.key === "Escape" && selection) {
        setSelection(null);
      } else if (!mod && !e.altKey) {
        const k = e.key.toLowerCase();
        if (k === "p") setTool("pen");
        else if (k === "h") setTool("hl");
        else if (k === "e") setTool("eraser");
        else if (k === "s") setTool("select");
        else if (k === "v") setTool("hand");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo, save, readOnly, selection, deleteSelection]);

  // Keep the screen on while taking notes in class.
  useEffect(() => {
    if (!awake || !("wakeLock" in navigator)) return;
    let lock: WakeLockSentinel | null = null;
    let stopped = false;
    const acquire = () => {
      if (stopped || document.visibilityState !== "visible") return;
      navigator.wakeLock.request("screen").then((l) => (lock = l), () => undefined);
    };
    acquire();
    document.addEventListener("visibilitychange", acquire);
    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", acquire);
      lock?.release().catch(() => undefined);
    };
  }, [awake]);

  const [status, setStatus] = useState<LessonStatus>(lesson.status);

  const rename = async () => {
    const t = title.trim();
    if (!t) return setTitle(lesson.title);
    if (!owner || t === lesson.title) return;
    try {
      await patch(`/api/lessons/${lesson.id}`, { title: t });
      lesson.title = t;
      document.title = `${t} · Lecta`;
    } catch (e) {
      toastError(e);
      setTitle(lesson.title);
    }
  };

  const summary = useMemo(
    () => ({
      id: lesson.id,
      course_id: lesson.course_id,
      course_name: lesson.course_name,
      course_guidelines: lesson.course_guidelines,
      chapter_id: lesson.chapter_id,
      slides: pages.filter((p) => p.kind === "slide").length,
      notes_pages: pages.filter((p) => p.notes.trim()).length,
      ink_pages: pages.filter((p) => p.ink.length).length,
    }),
    [lesson, pages],
  );

  const labels = useMemo(() => pages.map((_, i) => pageLabel(pages, i)), [pages]);
  const colors = tool === "hl" ? HL_COLORS : COLORS;
  const colorSel = tool === "hl" ? hlIdx : colorIdx;

  return (
    <div className="les" data-testid="lesson-editor">
      <header className="wsb">
        {owner && (
          <a className="btn ghost icon wsb-back" href="/admin/lessons" aria-label="Torna alle lezioni" title="Lezioni">
            <Icon name="arrow-left" />
          </a>
        )}
        <input
          className="les-title"
          type="text"
          value={title}
          readOnly={!owner}
          aria-label="Titolo della lezione"
          onChange={(e) => setTitle(e.target.value)}
          onBlur={rename}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        />
        <span className="les-course muted small">{lesson.course_name}</span>
        {owner && <LessonStatusPill id={lesson.id} status={status} editable onChange={setStatus} />}
        <div className="wsb-actions">
          {!readOnly && (
          <button type="button" className={`btn ghost icon les-save ${store.saveState}`} data-testid="save-state" role="status" aria-live="polite" onClick={() => void save()} title={`${SAVE_LABEL[store.saveState]}. Salva ora (Ctrl+S); il salvataggio automatico avviene ogni minuto.`}>
            <Icon name={store.saveState === "saved" ? "check" : store.saveState === "saving" ? "loader" : store.saveState === "pending" ? "save" : "alert-triangle"} className={store.saveState === "saving" ? "spin" : ""} />
            <span className="sr-only">{SAVE_LABEL[store.saveState]}</span>
          </button>
          )}
          {owner && lesson.generated_at && lesson.last_result?.chapters?.length ? (
            <a className="btn ghost hide-mobile" href={courseText(lesson.course_id, { chapter: lesson.last_result.chapters[0].chapter_id })} title="Apri il testo generato da questa lezione">
              <Icon name="book" />
              <span className="wsb-lbl">Testo del {fmtDay(lesson.generated_at)}</span>
            </a>
          ) : null}
          <Pop
            label="Scarica"
            className="ghost icon les-wide"
            summary={<Icon name="download" />}
          >
            <a href={`${source.base}/annotated.pdf`} download data-testid="download-annotated">
              <Icon name="file-text" />
              PDF con le tue scritte
            </a>
            <a href={`${source.base}/notes.md`} download>
              <Icon name="file" />
              Appunti (.md)
            </a>
          </Pop>
          {(owner || labHref) && (
            <a
              className="btn ghost"
              href={owner ? `/admin/courses/${lesson.course_id}/lessons/${lesson.number}/lab` : labHref!}
              data-testid="lesson-lab"
              title={
                !owner
                  ? "Apri il laboratorio di questa lezione"
                  : lesson.lab
                    ? `Apri il laboratorio di questa lezione (${lesson.lab.files} file)`
                    : "Crea il laboratorio di questa lezione: i file spiegati in classe, da commentare"
              }
            >
              <Icon name="flask" />
              <span className="wsb-lbl">Laboratorio</span>
            </a>
          )}
          {owner && (
            <button type="button" className="btn les-wide" onClick={() => setShowShare(true)} data-testid="share-open" title="Condividi la lezione con un link, in sola lettura o modificabile">
              <Icon name="link" />
              <span className="wsb-lbl">Condividi</span>
            </button>
          )}
          {owner && (
            <button
              type="button"
              className="btn primary wsb-review"
              onClick={async () => {
                await store.flushAll();
                setShowGenerate(true);
              }}
              data-testid="generate-open"
              title="Integra appunti e slide nel testo da studiare della materia"
            >
              Integra appunti
            </button>
          )}
          <button type="button" className="btn ghost icon les-wide" data-theme-toggle aria-label="Cambia tema" title="Tema chiaro / scuro">
            <Icon name="moon" className="i-moon" />
            <Icon name="sun" className="i-sun" />
          </button>
          <Pop label="Altre azioni" className="ghost icon" summary={<Icon name="more" />}>
            {/* On a phone the bar has no room for these: they move here. */}
            <a href={`${source.base}/annotated.pdf`} download className="les-narrow">
              <Icon name="file-text" />
              Scarica il PDF con le tue scritte
            </a>
            <a href={`${source.base}/notes.md`} download className="les-narrow">
              <Icon name="file" />
              Scarica gli appunti (.md)
            </a>
            {owner && (
              <button type="button" onClick={() => setShowShare(true)} className="les-narrow">
                <Icon name="link" />
                Condividi
              </button>
            )}
            <button type="button" data-theme-toggle className="les-narrow">
              <Icon name="moon" className="i-moon" />
              <Icon name="sun" className="i-sun" />
              Tema chiaro / scuro
            </button>
            <button type="button" onClick={() => setAwake(!awake)}>
              <Icon name={awake ? "check" : "monitor"} />
              Tieni lo schermo acceso{awake ? " (attivo)" : ""}
            </button>
            {owner && (
              <a href={`/admin/courses/${lesson.course_id}`}>
                <Icon name="book" />
                Apri la materia
              </a>
            )}
            {owner && (
              <a href="/admin/lessons">
                <Icon name="notebook" />
                Tutte le lezioni
              </a>
            )}
          </Pop>
        </div>
      </header>

      <div className="les-tools" role="toolbar" aria-label="Strumenti di scrittura">
        {!readOnly && (
          <>
        <div className="btn-group">
          <ToolButton id="pen" icon="pencil" label="Penna (P)" tool={tool} onPick={setTool} />
          <ToolButton id="hl" icon="highlighter" label="Evidenziatore (H)" tool={tool} onPick={setTool} />
          <ToolButton id="eraser" icon="eraser" label="Gomma (E)" tool={tool} onPick={setTool} />
          <ToolButton id="select" icon="select" label="Seleziona (S)" tool={tool} onPick={setTool} />
          <ToolButton id="hand" icon="hand" label="Scorri, senza scrivere (V)" tool={tool} onPick={setTool} />
        </div>
        {(tool === "pen" || tool === "hl") && (
          <>
            <div className="les-colors" role="group" aria-label="Colore">
              {colors.map((c, i) => (
                <button
                  key={c}
                  type="button"
                  className={`les-swatch ${colorSel === i ? "on" : ""}`}
                  style={{ background: c }}
                  aria-label={`Colore ${c}`}
                  aria-pressed={colorSel === i}
                  onClick={() => (tool === "hl" ? setHlIdx(i) : setColorIdx(i))}
                />
              ))}
            </div>
            <div className="les-sizes" role="group" aria-label="Spessore">
              {(tool === "hl" ? HL_WIDTHS : PEN_WIDTHS).map((w, i) => (
                <button
                  key={w}
                  type="button"
                  className={`les-size ${(tool === "hl" ? hlSize : penSize) === i ? "on" : ""}`}
                  aria-label={`Spessore ${i + 1}`}
                  aria-pressed={(tool === "hl" ? hlSize : penSize) === i}
                  onClick={() => (tool === "hl" ? setHlSize(i) : setPenSize(i))}
                >
                  <span style={{ width: 6 + i * 5, height: 6 + i * 5 }} />
                </button>
              ))}
            </div>
          </>
        )}
        {tool === "select" && (
          <>
            <span className="les-sel-hint muted small" data-testid="selection-count">
              {selection ? `${selection.strokes.length} ${selection.strokes.length === 1 ? "tratto selezionato" : "tratti selezionati"}` : "Clic su un tratto, o trascina un rettangolo"}
            </span>
            <button type="button" className="btn" onClick={deleteSelection} disabled={!selection} data-testid="delete-selection" title="Elimina i tratti selezionati (Canc)">
              <Icon name="trash" />
              <span className="les-lbl">Elimina</span>
            </button>
          </>
        )}
        <div className="btn-group">
          <button type="button" className="btn icon" onClick={undo} disabled={!store.canUndo} aria-label="Annulla" title="Annulla (Ctrl+Z)" data-testid="undo">
            <Icon name="undo" />
          </button>
          <button type="button" className="btn icon" onClick={redo} disabled={!store.canRedo} aria-label="Ripristina" title="Ripristina (Ctrl+Maiusc+Z)" data-testid="redo">
            <Icon name="redo" />
          </button>
        </div>
        <button
          type="button"
          className={`btn ${shapes ? "active" : ""}`}
          aria-pressed={shapes}
          onClick={() => setShapes(!shapes)}
          data-testid="shapes-toggle"
          title="Acceso: una linea, un rettangolo, un triangolo o un cerchio disegnati a mano vengono corretti (come in Xournal++). Le sottolineature diventano linee dritte."
        >
          <Icon name="shapes" />
          <span className="les-lbl">Forme</span>
        </button>
        <button
          type="button"
          className={`btn ${fingerDraws ? "active" : ""}`}
          aria-pressed={fingerDraws}
          onClick={() => setFingerDraws(!fingerDraws)}
          title="Acceso: si scrive anche con il dito. Spento: il dito fa scorrere la pagina e solo penna e mouse scrivono."
        >
          <Icon name="hand" />
          <span className="les-lbl">Dito scrive</span>
        </button>
          </>
        )}
        <span className="grow" />
        <div className="btn-group les-pager">
          <button type="button" className="btn icon" onClick={() => goTo(Math.max(0, current - 1))} disabled={current <= 0} aria-label="Pagina precedente" title="Pagina precedente">
            <Icon name="chevron-up" />
          </button>
          <span className="les-pageno mono" data-testid="page-number">
            {pages.length ? current + 1 : 0} / {pages.length}
          </span>
          <button type="button" className="btn icon" onClick={() => goTo(Math.min(pages.length - 1, current + 1))} disabled={current >= pages.length - 1} aria-label="Pagina successiva" title="Pagina successiva">
            <Icon name="chevron-down" />
          </button>
        </div>
        {!readOnly && (
        <button
          type="button"
          className="btn icon"
          onClick={() => pages[current] && actions.addBlankAfter(pages[current].id)}
          aria-label="Aggiungi una pagina bianca dopo questa"
          title="Aggiungi una pagina bianca dopo questa"
          data-testid="add-page"
        >
          <Icon name="plus" />
        </button>
        )}
        <Seg
          size="sm"
          value={layout}
          onChange={setLayout}
          options={[
            { key: "side", label: <Icon name="columns" title="Slide e appunti affiancati" /> },
            { key: "stack", label: <Icon name="rows" title="Appunti sotto la slide" /> },
            { key: "slides", label: <Icon name="image" title="Solo slide" /> },
          ]}
        />
        {!readOnly && (
          <button type="button" className={`btn icon ${preview ? "active" : ""}`} aria-pressed={preview} onClick={() => setPreview(!preview)} aria-label="Anteprima degli appunti" title="Anteprima degli appunti (Markdown)">
            <Icon name="eye" />
          </button>
        )}
        <div className="btn-group">
          <button type="button" className="btn icon" onClick={() => zoomTo(stepZoom(zoom, -1))} disabled={zoom <= ZOOM_MIN} aria-label="Riduci" title="Riduci (anche con due dita, o Ctrl + rotella)">
            <Icon name="minus" />
          </button>
          <button type="button" className="btn les-zoom mono" onClick={() => zoomTo(1)} data-testid="zoom-level" title="Torna al 100%">
            {Math.round(zoom * 100)}%
          </button>
          <button type="button" className="btn icon" onClick={() => zoomTo(stepZoom(zoom, 1))} disabled={zoom >= ZOOM_MAX} aria-label="Ingrandisci" title="Ingrandisci (anche con due dita, o Ctrl + rotella)">
            <Icon name="plus" />
          </button>
        </div>
      </div>

      <div className="les-body">
        <div className="les-scroll" ref={scroller} data-testid="lesson-scroll">
          {pdfError && <div className="alert danger">Non riesco a leggere le slide: {pdfError}</div>}
          {store.gone && (
            <div className="alert danger" role="alert">
              Questo link non è più valido: chi l’ha condiviso l’ha revocato o ne ha fatto uno nuovo.
            </div>
          )}
          <div className="les-pages" style={{ ["--zoom" as string]: zoom }}>
            {pages.map((p, i) => (
              <PageRow
                key={p.id}
                page={p}
                index={i}
                total={pages.length}
                doc={doc}
                draw={draw}
                layout={layout}
                zoom={zoom}
                preview={preview || readOnly}
                readOnly={readOnly}
                scroller={scroller}
                actions={actions}
                label={labels[i]}
                selected={selection?.pid === p.id ? selection.strokes : null}
              />
            ))}
            {!readOnly && (
              <button type="button" className="btn les-add-end" onClick={() => actions.addBlankAfter(null)}>
                <Icon name="plus" />
                Pagina bianca in fondo
              </button>
            )}
          </div>
        </div>
      </div>
      {askRemove !== null && (
        <Confirm
          title={pagesRef.current.find((p) => p.id === askRemove)?.kind === "slide" ? "Togliere la slide?" : "Togliere la pagina?"}
          danger
          confirmLabel="Togli"
          message={
            <>
              «{labels[pages.findIndex((p) => p.id === askRemove)] ?? "Questa pagina"}» esce dalla lezione con i suoi appunti e le sue scritte
              {pagesRef.current.find((p) => p.id === askRemove)?.kind === "slide" ? " e non entra nel testo generato" : ""}. Puoi annullare con Ctrl+Z.
            </>
          }
          onConfirm={() => removePage(askRemove)}
          onClose={() => setAskRemove(null)}
        />
      )}
      {showShare && <ShareDialog lessonId={lesson.id} onChange={(n) => setHasShares(n > 0)} onClose={() => setShowShare(false)} />}
      {showGenerate && <GenerateDialog lesson={summary} beforeSend={store.flushAll} onClose={() => setShowGenerate(false)} />}
    </div>
  );
}

function ToolButton({ id, icon, label, tool, onPick }: { id: Tool; icon: string; label: string; tool: Tool; onPick: (t: Tool) => void }) {
  return (
    <button type="button" className={`btn icon ${tool === id ? "active" : ""}`} aria-pressed={tool === id} aria-label={label} title={label} data-testid={`tool-${id}`} onClick={() => onPick(id)}>
      <Icon name={icon} />
    </button>
  );
}
