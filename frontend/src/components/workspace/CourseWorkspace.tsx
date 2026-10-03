// The course workspace: the draft of the document in the middle, the outline on the left and the AI
// assistant on the right. /admin/courses/{id}/testo?chapter={id} scrolls to a chapter, ?chat=1 opens the
// assistant, ?ask=… prefills its message box.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { get } from "../../lib/api";
import { Icon } from "../icons";
import type { Course } from "../types";
import { Modal, Seg, toast, toastError, useLocalStorage } from "../ui";
import AiPanel from "./AiPanel";
import { compileFull, errorsText } from "./compile";
import type { ScopeView } from "./Composer";
import CourseSettings from "./CourseSettings";
import DocView, { type FlashRequest, type ScrollRequest } from "./DocView";
import type { MessageCtx } from "./MessageView";
import Outline from "./Outline";
import PdfPane from "./PdfPane";
import Pop from "./Pop";
import type { DraftSelection } from "./selection";
import type { SelectionAction } from "./SelectionToolbar";
import TopBar from "./TopBar";
import type { ChangedFile, DraftChapter, FlashTarget, Mode, Scope, SelectionScope } from "./types";
import { recentAiChapters, useAssistant } from "./useAssistant";
import { PHONE, useMedia } from "./util";

const COMPACT = "(max-width: 1180px)";
/** Chapters typeset at the same time (the compile service runs two jobs at once; the rest come from the cache). */
const DRAFT_PARALLEL = 3;

async function inPool<T>(items: T[], n: number, fn: (item: T) => Promise<void>): Promise<void> {
  let next = 0;
  const worker = async () => {
    while (next < items.length) await fn(items[next++]);
  };
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, worker));
}

const REMOVE_PROMPT =
  "Rimuovi questo passaggio dal testo. Se serve, fai piccole correzioni di formattazione al resto (spaziature, elenchi, riferimenti, " +
  "passaggi di raccordo) perché il capitolo resti coerente: nient'altro.";

const INTENT_MODE: Record<string, Mode> = { explain: "explain", fix: "edit" };

const targetsOf = (files: ChangedFile[]): FlashTarget[] =>
  files.flatMap((f) => (f.chapter_id == null ? [] : f.hunks.map((h) => ({ chapterId: f.chapter_id!, from: h.from_line, to: h.to_line }))));

export default function CourseWorkspace({ courseId }: { courseId: number }) {
  const [course, setCourse] = useState<Course | null>(null);
  const [preview, setPreview] = useState<DraftChapter[] | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [flash, setFlash] = useState<FlashRequest | null>(null);
  const [scrollTo, setScrollTo] = useState<ScrollRequest | null>(null);
  const [activeChapter, setActiveChapter] = useState<number | null>(null);
  const [view, setView] = useState<"draft" | "pdf">("draft");
  const [pdfSeen, setPdfSeen] = useState(false);
  const [settings, setSettings] = useState(false);
  const [compiling, setCompiling] = useState(false);

  // layout
  const phone = useMedia(PHONE);
  const compact = useMedia(COMPACT);
  const [railOpen, setRailOpen] = useLocalStorage("lecta.ws.rail", true);
  const [aiOpen, setAiOpen] = useLocalStorage("lecta.ws.ai", true);
  const [aiWidth, setAiWidth] = useLocalStorage<number | null>("lecta.ws.aiw", null);
  const [drawer, setDrawer] = useState(false);
  const [mtab, setMtab] = useState<"doc" | "ai">("doc");

  // what the next message is about
  // intent: the chat was opened on a selection (Spiega / Correggi) and waits for what the user wants to know or fix.
  const [pinned, setPinned] = useState<{ chapterId: number; selection?: SelectionScope; intent?: "explain" | "fix" } | null>(null);
  const [followView, setFollowView] = useState(true);
  const [focusNonce, setFocusNonce] = useState(0);
  const [prefill, setPrefill] = useState<{ nonce: number; text: string } | null>(null);

  // ------------------------------------------------------------------ the draft

  const inflight = useRef(false);
  const dirty = useRef(false);
  const pending = useRef<FlashTarget[]>([]);
  const flashed = useRef(new Set<string>());
  const flashId = useRef(0);
  const timer = useRef(0);
  const first = useRef(true);

  const refresh = useCallback(async () => {
    if (inflight.current) {
      dirty.current = true;
      return;
    }
    inflight.current = true;
    try {
      do {
        dirty.current = false;
        const c = await get<Course>(`/api/courses/${courseId}`);
        const chs = [...(c.chapters ?? [])].sort((a, b) => a.position - b.position);
        c.chapters = chs;
        setCourse(c);
        // What is shown stays until its new version arrives; a new chapter waits for LaTeX with a spinner.
        setPreview((prev) =>
          chs.map((ch) => {
            const info = { id: ch.id, title: ch.title, path: ch.path, position: ch.position };
            const old = prev?.find((p) => p.chapter.id === ch.id);
            return old ? { ...old, chapter: info } : { chapter: info, width: 455, blocks: null, toc: [], warnings: [] };
          }),
        );
        setPreviewError(null);
        // Every chapter is typeset (or comes from the cache) on its own: each one shows up as soon as it is ready.
        await inPool(chs, DRAFT_PARALLEL, async (ch) => {
          let d: DraftChapter;
          try {
            d = await get<DraftChapter>(`/api/courses/${courseId}/chapters/${ch.id}/draft`);
          } catch (e) {
            d = { chapter: { id: ch.id, title: ch.title, path: ch.path, position: ch.position }, width: 455, blocks: [], toc: [], warnings: [], error: e instanceof Error ? e.message : String(e) };
          }
          setPreview((prev) => prev && prev.map((p) => (p.chapter.id === ch.id ? d : p)));
        });
        setRevision((r) => r + 1);
        if (pending.current.length) {
          setFlash({ id: ++flashId.current, targets: pending.current, scroll: false });
          pending.current = [];
        }
      } while (dirty.current);
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : String(e));
    } finally {
      inflight.current = false;
    }
  }, [courseId]);

  useEffect(() => {
    void refresh();
    // pick up changes made elsewhere (an import that finished) when coming back to the tab
    let last = Date.now();
    const onVisible = () => {
      if (document.visibilityState === "visible" && Date.now() - last > 30_000) void refresh();
      last = Date.now();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [refresh]);

  const onAiChange = useCallback(
    (files: ChangedFile[], final: boolean) => {
      const fresh = targetsOf(files).filter((t) => {
        const k = `${t.chapterId}:${t.from}-${t.to}`;
        if (flashed.current.has(k)) return false;
        flashed.current.add(k);
        return true;
      });
      pending.current.push(...fresh);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => void refresh(), final ? 80 : 450);
    },
    [refresh],
  );
  const assistant = useAssistant({ courseId, onChange: onAiChange });
  const aiChanged = useMemo(() => recentAiChapters(assistant.messages), [assistant.messages]);

  // ------------------------------------------------------------------ navigation

  const showDoc = useCallback(() => {
    setView("draft");
    setMtab("doc");
  }, []);

  const goto = useCallback(
    (chapterId: number, sectionId?: string) => {
      showDoc();
      setDrawer(false);
      setScrollTo({ nonce: Date.now(), chapterId, sectionId });
    },
    [showDoc],
  );

  const jump = useCallback(
    (chapterId: number | null, targets?: FlashTarget[]) => {
      showDoc();
      if (chapterId == null) return;
      if (targets && targets.length) setFlash({ id: ++flashId.current, targets, scroll: true });
      else goto(chapterId);
    },
    [showDoc, goto],
  );

  // deep links, once the draft is there
  useEffect(() => {
    if (!preview || !first.current) return;
    first.current = false;
    const q = new URLSearchParams(location.search);
    const ch = Number(q.get("chapter"));
    if (ch && preview.some((p) => p.chapter.id === ch)) setTimeout(() => setScrollTo({ nonce: Date.now(), chapterId: ch }), 60);
    const ask = q.get("ask");
    if (q.get("chat") || ask) {
      setAiOpen(true);
      setMtab("ai");
    }
    if (ask) setPrefill({ nonce: Date.now(), text: ask });
  }, [preview, setAiOpen]);

  // ------------------------------------------------------------------ asking the assistant

  const openAi = useCallback(() => {
    setAiOpen(true);
    setMtab("ai");
  }, [setAiOpen]);

  const chapters = course?.chapters ?? [];
  const effective = pinned ? pinned.chapterId : followView ? activeChapter : null;

  const ask: MessageCtx["onAsk"] = useCallback(
    async (text, opts = {}) => {
      let scope: Scope;
      if (opts.scope) scope = opts.scope;
      else {
        scope = {};
        if (!opts.noScope) {
          const chapterId = opts.chapterId !== undefined ? opts.chapterId : effective;
          if (chapterId != null && chapters.some((c) => c.id === chapterId)) scope.chapter_id = chapterId;
          if (pinned?.selection && opts.chapterId === undefined) scope.selection = pinned.selection;
        }
        const mode = opts.mode ?? (pinned && opts.chapterId === undefined ? INTENT_MODE[pinned.intent ?? ""] : undefined);
        if (mode) scope.mode = mode;
      }
      flashed.current.clear();
      openAi();
      const ok = await assistant.send(text, scope);
      if (ok) setPinned(null);
      return ok;
    },
    [assistant, effective, pinned, chapters, openAi],
  );

  const askSelection = (a: SelectionAction, s: DraftSelection) => {
    if (a === "remove") {
      flashed.current.clear();
      openAi();
      void assistant.send(REMOVE_PROMPT, { chapter_id: s.chapterId, selection: s.selection, mode: "edit" });
      return;
    }
    // Spiega / Correggi: the assistant gets the passage as context and waits for the user's question.
    setPinned({ chapterId: s.chapterId, selection: s.selection, intent: a });
    openAi();
    setFocusNonce((n) => n + 1);
  };

  const askChapter = (chapterId: number) => {
    setPinned({ chapterId });
    setDrawer(false);
    openAi();
    setFocusNonce((n) => n + 1);
  };

  const fixBuild = (text: string) => void ask(text, { mode: "edit", noScope: true });

  const scopeView: ScopeView | null = useMemo(() => {
    if (effective == null) return null;
    const i = chapters.findIndex((c) => c.id === effective);
    if (i < 0) return null;
    return { chapterId: effective, chapterLabel: `Capitolo ${i + 1}`, chapterTitle: chapters[i].title, selection: pinned?.selection ?? null, intent: pinned?.intent ?? null, pinned: !!pinned };
  }, [effective, chapters, pinned]);

  const clearScope = () => {
    if (pinned) setPinned(null);
    else setFollowView(false);
  };

  const ctx: MessageCtx = useMemo(() => ({ chapters, onJump: jump, onAsk: ask, onUndo: assistant.undo }), [chapters, jump, ask, assistant.undo]);

  // ------------------------------------------------------------------ downloads

  const downloadPdf = async () => {
    if (compiling) return;
    const w = window.open("", "_blank"); // opened now, while the click still counts
    setCompiling(true);
    try {
      const r = await compileFull(courseId);
      if (r.pdf_url) {
        if (w) w.location.href = r.pdf_url;
        else window.location.href = r.pdf_url;
        if (r.status !== "ok") toast("Il PDF è stato compilato con errori: vedi la scheda PDF", "error");
      } else {
        w?.close();
        const errs = errorsText(r, 3);
        toast(`Compilazione non riuscita${errs ? `:\n${errs}` : ""}`, "error");
        setPdfSeen(true);
      }
    } catch (e) {
      w?.close();
      toastError(e);
    } finally {
      setCompiling(false);
    }
  };

  // ------------------------------------------------------------------ resizing the assistant

  const resize = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => setAiWidth(Math.round(Math.min(Math.max(window.innerWidth - ev.clientX, 340), Math.min(760, window.innerWidth * 0.62))));
    const up = () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
  };

  // ------------------------------------------------------------------ render

  if (!course) {
    return (
      <div className="ws ws-loading">
        {previewError ? (
          <div className="alert danger">
            <Icon name="alert-circle" />
            <span>{previewError}</span>
          </div>
        ) : (
          <div className="muted row">
            <Icon name="loader" className="spin" />
            Apro la materia…
          </div>
        )}
      </div>
    );
  }

  const warnings = (preview ?? []).flatMap((p) => [
    ...p.warnings.map((text) => ({ chapter: p.chapter.title, text })),
    ...(p.blocks ?? []).filter((b) => b.error).map((b) => ({ chapter: p.chapter.title, text: `Errore LaTeX, ${b.error}` })),
  ]);
  const showRail = compact ? drawer : railOpen;
  const showAi = phone ? mtab === "ai" : aiOpen;
  const showDocPane = !phone || mtab === "doc";

  const empty = (
    <div className="doc-empty">
      <span className="doc-empty-ico">
        <Icon name="book" />
      </span>
      <h2>Questa materia è ancora vuota</h2>
      <p>Prendi appunti a lezione, sulle slide o su pagine bianche: Lecta li trasforma in un testo da studiare. Oppure lascia fare all’assistente a partire dalle fonti che hai già.</p>
      <div className="row" style={{ justifyContent: "center" }}>
        <a className="btn primary" href={`/admin/lessons?new=1&course=${courseId}`}>
          <Icon name="notebook" />
          Nuova lezione
        </a>
        <button type="button" className="btn" onClick={() => void ask("Parti dalle fonti caricate: crea i capitoli e scrivi una prima bozza del corso.", { mode: "edit", noScope: true })}>
          <Icon name="sparkles" />
          Scrivi una prima bozza
        </button>
      </div>
    </div>
  );

  return (
    <div className={`ws ${phone ? "is-phone" : ""}`} data-testid="workspace" style={aiWidth ? ({ "--ai-w": `${aiWidth}px` } as React.CSSProperties) : undefined}>
      <TopBar
        course={course}
        busy={assistant.busy}
        compiling={compiling}
        railOpen={showRail}
        aiOpen={aiOpen}
        onToggleRail={() => (compact ? setDrawer(!drawer) : setRailOpen(!railOpen))}
        onToggleAi={() => (phone ? setMtab(mtab === "ai" ? "doc" : "ai") : setAiOpen(!aiOpen))}
        onDownloadPdf={downloadPdf}
        onSettings={() => setSettings(true)}
        onPublishChanged={() => void refresh()}
      />

      <div className={`ws-body ${showRail && !compact ? "with-rail" : ""} ${showAi && !phone ? "with-ai" : ""}`}>
        {showRail && (
          <>
            {compact && <div className="ws-scrim" onClick={() => setDrawer(false)} aria-hidden="true" />}
            <div className={`ws-rail ${compact ? "drawer" : ""}`}>
              <Outline
                courseId={courseId}
                chapters={chapters}
                preview={preview}
                activeChapterId={activeChapter}
                aiChanged={aiChanged}
                onGoto={goto}
                onAskChapter={askChapter}
                onStructure={() => void refresh()}
              />
            </div>
          </>
        )}

        <main className={`ws-doc ${showDocPane ? "" : "off"}`}>
          <div className="doc-bar">
            {compact && (
              <button type="button" className="btn sm ghost" onClick={() => setDrawer(true)}>
                <Icon name="list" />
                Indice
              </button>
            )}
            <Seg<"draft" | "pdf">
              size="sm"
              value={view}
              onChange={(v) => {
                setView(v);
                if (v === "pdf") setPdfSeen(true);
              }}
              options={[
                { key: "draft", label: "Bozza" },
                { key: "pdf", label: "PDF" },
              ]}
            />
            <span className="grow" />
            {warnings.length > 0 && view === "draft" && (
              <Pop
                label="Avvisi della composizione LaTeX"
                className="sm ghost"
                menuClass="doc-warn-pop"
                summary={
                  <>
                    <Icon name="alert-triangle" />
                    {warnings.length} {warnings.length === 1 ? "avviso" : "avvisi"}
                  </>
                }
              >
                {warnings.map((x, i) => (
                  <div key={i} className="doc-warn-item">
                    <span className="tiny muted">{x.chapter}</span>
                    <span className="small">{x.text}</span>
                  </div>
                ))}
              </Pop>
            )}
          </div>
          <div className="doc-stages">
          <div className={`doc-stage ${view === "draft" ? "" : "off"}`}>
            <DocView
              chapters={preview}
              loading={preview === null}
              error={previewError}
              flash={flash}
              scrollTo={scrollTo}
              empty={empty}
              onActiveChapter={setActiveChapter}
              onSelectionAction={askSelection}
            />
          </div>
          {pdfSeen && (
            <div className={`doc-stage ${view === "pdf" ? "" : "off"}`}>
              <PdfPane courseId={courseId} revision={revision} active={view === "pdf"} onFix={fixBuild} />
            </div>
          )}
          </div>
        </main>

        {!phone && showAi && (
          <div
            className="ws-resizer"
            role="separator"
            aria-orientation="vertical"
            aria-label="Larghezza dell’assistente"
            tabIndex={0}
            onPointerDown={resize}
            onKeyDown={(e) => {
              if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                e.preventDefault();
                const cur = aiWidth ?? Math.min(620, Math.max(380, window.innerWidth * 0.4));
                setAiWidth(Math.round(Math.min(760, Math.max(340, cur + (e.key === "ArrowLeft" ? 32 : -32)))));
              }
            }}
          />
        )}
        <div className={`ws-ai ${showAi ? "" : "off"}`}>
          <AiPanel
            assistant={assistant}
            ctx={ctx}
            scope={scopeView}
            open={showAi}
            focusNonce={focusNonce}
            prefill={prefill}
            onClearScope={clearScope}
            onClose={phone ? undefined : () => setAiOpen(false)}
          />
        </div>
      </div>

      {phone && (
        <nav className="ws-tabs" aria-label="Vista">
          <button type="button" className={mtab === "doc" ? "active" : ""} onClick={() => setMtab("doc")} aria-current={mtab === "doc"}>
            <Icon name="file-text" />
            Documento
          </button>
          <button type="button" className={mtab === "ai" ? "active" : ""} onClick={() => setMtab("ai")} aria-current={mtab === "ai"}>
            <Icon name="sparkles" />
            Assistente
            {assistant.busy && <span className="wsb-pulse" aria-hidden="true" />}
          </button>
        </nav>
      )}

      {settings && (
        <Modal title="Impostazioni della materia" wide onClose={() => setSettings(false)}>
          <CourseSettings
            course={course}
            onSaved={() => {
              window.dispatchEvent(new Event("lecta:tree-changed"));
              void refresh();
            }}
          />
        </Modal>
      )}
    </div>
  );
}
