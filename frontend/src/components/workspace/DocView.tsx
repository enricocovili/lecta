// The draft of the whole document: every chapter typeset by LaTeX, block by block (paragraphs, headings, environments
// as SVG pictures), in one continuous scroll. A click picks a block (Shift+click extends the pick) and opens the
// toolbar to ask the AI about it; a double click (or F2) opens the block's text in place, to write anything there.
// Blocks are flashed where the AI just worked. Someone reading another's published course picks a block to comment
// on it or suggest a change (decision 129); a marker on a block shows the notes they left there.
import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { post, put } from "../../lib/api";
import { Icon } from "../icons";
import { toast } from "../ui";
import { NoteComposer, NoteList, useDraftNotes } from "./DraftNotes";
import { EditBar, EditBox } from "./EditBox";
import { placeNotes, type DraftNote, type NoteKind } from "./notes";
import { pickBlock, pickScope, type BlockPick, type DraftSelection } from "./selection";
import SelectionToolbar, { type SelectionAction, type ToolbarAction } from "./SelectionToolbar";
import type { DraftBlock, DraftChapter, FlashTarget } from "./types";

export interface FlashRequest {
  id: number;
  targets: FlashTarget[];
  scroll: boolean;
}

export interface ScrollRequest {
  nonce: number;
  chapterId: number;
  sectionId?: string;
}

const NOTE_ACTIONS: ToolbarAction<NoteKind>[] = [
  { key: "comment", label: "Commenta", title: "Scrivi un commento su questo blocco: lo legge chi scrive la materia" },
  { key: "suggestion", label: "Suggerisci modifica", title: "Riscrivi il testo di questo blocco: chi scrive la materia accetta o rifiuta la modifica" },
];

const Block = memo(function Block({
  b,
  index,
  width,
  picked,
  still,
  notes = 0,
  notesOpen = false,
}: {
  b: DraftBlock;
  index: number;
  width: number;
  picked: boolean;
  still: boolean;
  /** How many of the reader's notes are on it: a marker that shows them. */
  notes?: number;
  notesOpen?: boolean;
}) {
  const failed = !!b.error;
  return (
    <div
      className={`doc-block${b.heading ? " doc-heading" : ""}${picked ? " picked" : ""}${failed ? " failed" : ""}${notes ? " noted" : ""}`}
      data-line={b.start}
      data-end={b.end}
      data-index={index}
      id={b.id}
      tabIndex={still ? undefined : b.pages.length || failed ? 0 : -1}
    >
      {b.pages.map((p) => (
        <img
          key={p.url}
          src={p.url}
          alt={(b.src ?? "").slice(0, 400)}
          className={p.raster ? "raster" : undefined}
          draggable={false}
          loading="lazy"
          decoding="async"
          style={{ width: `${(p.w / width) * 100}%`, marginLeft: `${(p.x / width) * 100}%`, aspectRatio: `${p.w} / ${p.h}` }}
        />
      ))}
      {failed && (
        <div className="doc-block-error">
          <span className="small">
            <Icon name="alert-triangle" /> Errore LaTeX, {b.error}
          </span>
          {!b.pages.length && <pre>{b.src}</pre>}
        </div>
      )}
      {notes > 0 && (
        <button
          type="button"
          className="doc-note-mark"
          data-testid="note-mark"
          aria-expanded={notesOpen}
          aria-label={notes === 1 ? "Una tua nota su questo blocco" : `${notes} tue note su questo blocco`}
          title="Le tue note su questo blocco"
        >
          <Icon name="message" />
          {notes > 1 && <span>{notes}</span>}
        </button>
      )}
    </div>
  );
});

interface Editing {
  chapterId: number;
  index: number;
  block: DraftBlock;
}

/** A block's text written in place: Ctrl+Enter (or «Salva») writes it into the chapter, Esc leaves it as it was. */
function BlockEditor({ courseId, edit, onDone }: { courseId: number; edit: Editing; onDone: (d: DraftChapter | null) => void }) {
  const [text, setText] = useState(edit.block.src ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (saving) return;
    if (text === (edit.block.src ?? "")) return onDone(null);
    setSaving(true);
    setError(null);
    try {
      const { start, end, src } = edit.block;
      onDone(await put<DraftChapter>(`/api/courses/${courseId}/chapters/${edit.chapterId}/block`, { start, end, src: src ?? "", text }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  };

  return (
    <div className="doc-edit" data-testid="block-editor">
      <EditBox value={text} onChange={setText} label="Testo del blocco" disabled={saving} autoFocus onSubmit={() => void save()} onCancel={() => onDone(null)} />
      <EditBar
        hint="Ctrl+Invio salva · Esc annulla · vuoto toglie il blocco"
        error={error}
        busy={saving}
        submitLabel="Salva"
        onSubmit={() => void save()}
        onCancel={() => onDone(null)}
      />
    </div>
  );
}

/** Nothing written yet: at most the chapter title. */
const isHollow = (c: DraftChapter) => !!c.blocks && c.blocks.every((b) => b.heading === "chapter" || (!b.pages.length && !b.error));

/** Blocks of a chapter that cover some of the lines [from, to]. */
function blocksCovering(section: HTMLElement, from: number, to: number): HTMLElement[] {
  return Array.from(section.querySelectorAll<HTMLElement>(".doc-block")).filter((el) => Number(el.dataset.line) <= to && Number(el.dataset.end) >= from);
}

/** The viewport rectangle around the picked blocks. */
function pickRect(root: HTMLElement, pick: BlockPick): DraftSelection["rect"] | null {
  const sec = root.querySelector<HTMLElement>(`[data-chapter-id="${pick.chapterId}"]`);
  const a = sec?.querySelector<HTMLElement>(`.doc-block[data-index="${pick.from}"]`);
  const b = sec?.querySelector<HTMLElement>(`.doc-block[data-index="${pick.to}"]`);
  if (!a || !b) return null;
  const ra = a.getBoundingClientRect();
  const rb = b.getBoundingClientRect();
  return { top: ra.top, bottom: rb.bottom, left: Math.min(ra.left, rb.left), right: Math.max(ra.right, rb.right) };
}

export default function DocView({
  courseId,
  chapters,
  loading,
  error,
  flash,
  scrollTo,
  empty,
  onActiveChapter,
  onSelectionAction,
  onBlockWritten,
  readOnly = false,
  notes: showNotes = false,
  canNote = false,
}: {
  courseId: number;
  chapters: DraftChapter[] | null;
  loading: boolean;
  error: string | null;
  flash: FlashRequest | null;
  scrollTo: ScrollRequest | null;
  empty: ReactNode;
  onActiveChapter: (id: number) => void;
  onSelectionAction: (a: SelectionAction, sel: DraftSelection) => void;
  /** A block was written in place: the chapter typeset again. */
  onBlockWritten: (d: DraftChapter) => void;
  /** Only to read: blocks are neither picked (the assistant's actions) nor written in place. */
  readOnly?: boolean;
  /** Show the user's own notes on the blocks (a reader's). */
  notes?: boolean;
  /** Read-only, and a click picks a block to comment on or suggest a change to (a published course). */
  canNote?: boolean;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const article = useRef<HTMLElement>(null);
  const [pick, setPick] = useState<BlockPick | null>(null);
  const [rect, setRect] = useState<DraftSelection["rect"] | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [composing, setComposing] = useState<(Editing & { kind: NoteKind }) | null>(null);
  const [shown, setShown] = useState<{ chapterId: number; index: number } | null>(null);
  const noting = readOnly && canNote;
  const still = readOnly && !noting;
  const { notes, saved, removed } = useDraftNotes(courseId, showNotes);
  const activeRef = useRef<number | null>(null);

  // -- which chapter is being read
  const spy = useCallback(() => {
    const sc = scroller.current;
    const art = article.current;
    if (!sc || !art) return;
    const top = sc.getBoundingClientRect().top + 96;
    let current: number | null = null;
    for (const s of Array.from(art.querySelectorAll<HTMLElement>("[data-chapter-id]"))) {
      if (s.getBoundingClientRect().top <= top) current = Number(s.dataset.chapterId);
      else break;
    }
    const all = art.querySelectorAll<HTMLElement>("[data-chapter-id]");
    // at the very end the last chapter counts as being read, even if it is too short to reach the top
    if (all.length && sc.scrollTop > 0 && sc.scrollHeight - sc.scrollTop - sc.clientHeight < 6) current = Number(all[all.length - 1].dataset.chapterId);
    if (current === null && all.length) current = Number(all[0].dataset.chapterId);
    if (current !== null && current !== activeRef.current) {
      activeRef.current = current;
      onActiveChapter(current);
    }
  }, [onActiveChapter]);

  // -- keep the toolbar on the picked blocks while reading on
  const place = useCallback(() => {
    setRect(pick && article.current ? pickRect(article.current, pick) : null);
  }, [pick]);
  useEffect(place, [place, chapters]);

  useEffect(() => {
    const sc = scroller.current;
    if (!sc) return;
    let raf = 0;
    const on = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        spy();
        place();
      });
    };
    sc.addEventListener("scroll", on, { passive: true });
    window.addEventListener("resize", on);
    return () => {
      sc.removeEventListener("scroll", on);
      window.removeEventListener("resize", on);
      cancelAnimationFrame(raf);
    };
  }, [spy, place]);
  useEffect(spy, [chapters, spy]);

  // A pick whose blocks are gone (the chapter changed under it) is dropped.
  useEffect(() => {
    if (!pick) return;
    const ch = chapters?.find((c) => c.chapter.id === pick.chapterId);
    if (!ch?.blocks || pick.to >= ch.blocks.length) setPick(null);
  }, [chapters, pick]);

  // -- scroll to a chapter / section (outline, deep link, change card)
  useEffect(() => {
    if (!scrollTo || !article.current) return;
    const sec = article.current.querySelector<HTMLElement>(`[data-chapter-id="${scrollTo.chapterId}"]`);
    if (!sec) return;
    const target = (scrollTo.sectionId && sec.querySelector<HTMLElement>(`[id="${CSS.escape(scrollTo.sectionId)}"]`)) || sec;
    target.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [scrollTo]);

  // -- flash what the AI changed
  useEffect(() => {
    if (!flash || !article.current) return;
    const marked: HTMLElement[] = [];
    for (const t of flash.targets) {
      const sec = article.current.querySelector<HTMLElement>(`[data-chapter-id="${t.chapterId}"]`);
      if (sec) marked.push(...blocksCovering(sec, t.from, t.to));
    }
    marked.forEach((el) => el.classList.add("doc-flash"));
    if (flash.scroll && marked[0]) marked[0].scrollIntoView({ block: "center", behavior: "smooth" });
    const t = window.setTimeout(() => marked.forEach((el) => el.classList.remove("doc-flash")), 3200);
    return () => {
      window.clearTimeout(t);
      marked.forEach((el) => el.classList.remove("doc-flash"));
    };
  }, [flash]);

  // -- picking blocks
  const pickAt = (target: EventTarget | null, extend: boolean) => {
    const el = target instanceof Element ? target.closest<HTMLElement>(".doc-block") : null;
    const sec = el?.closest<HTMLElement>("[data-chapter-id]");
    if (!el || !sec || el.tabIndex < 0) {
      setPick(null);
      return;
    }
    setPick((cur) => pickBlock(cur, Number(sec.dataset.chapterId), Number(el.dataset.index), extend));
  };

  const editAt = (target: EventTarget | null) => {
    const el = target instanceof Element ? target.closest<HTMLElement>(".doc-block") : null;
    const sec = el?.closest<HTMLElement>("[data-chapter-id]");
    if (!el || !sec || el.tabIndex < 0) return;
    const chapterId = Number(sec.dataset.chapterId);
    const index = Number(el.dataset.index);
    const block = chapters?.find((c) => c.chapter.id === chapterId)?.blocks?.[index];
    if (!block) return;
    setPick(null);
    window.getSelection()?.removeAllRanges();
    setEditing({ chapterId, index, block });
  };

  const edited = (d: DraftChapter | null) => {
    const at = editing;
    setEditing(null);
    if (d) onBlockWritten(d);
    // back on the block (or where it was) for the keyboard
    if (!at) return;
    requestAnimationFrame(() => {
      const sec = article.current?.querySelector<HTMLElement>(`[data-chapter-id="${at.chapterId}"]`);
      sec?.querySelector<HTMLElement>(`.doc-block[data-index="${at.index}"]`)?.focus({ preventScroll: true });
    });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPick(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const sel: DraftSelection | null = useMemo(() => {
    if (!pick || !rect) return null;
    const blocks = chapters?.find((c) => c.chapter.id === pick.chapterId)?.blocks;
    const scope = blocks && pickScope(blocks, pick);
    return scope ? { chapterId: pick.chapterId, selection: scope, rect } : null;
  }, [pick, rect, chapters]);

  // -- the reader's notes, on their blocks
  const placed = useMemo(() => {
    const m = new Map<number, ReturnType<typeof placeNotes>>();
    for (const c of chapters ?? []) {
      const mine = notes.filter((n) => n.chapter_id === c.chapter.id);
      if (mine.length && c.blocks) m.set(c.chapter.id, placeNotes(c.blocks, mine));
    }
    return m;
  }, [chapters, notes]);

  const markAt = (target: Element) => {
    const el = target.closest<HTMLElement>(".doc-block");
    const sec = el?.closest<HTMLElement>("[data-chapter-id]");
    if (!el || !sec) return;
    const at = { chapterId: Number(sec.dataset.chapterId), index: Number(el.dataset.index) };
    setPick(null);
    setShown((cur) => (cur && cur.chapterId === at.chapterId && cur.index === at.index ? null : at));
  };

  const noteAct = (kind: NoteKind) => {
    if (!pick) return;
    const block = chapters?.find((c) => c.chapter.id === pick.chapterId)?.blocks?.[pick.from];
    setPick(null);
    if (!block) return;
    setShown(null);
    setComposing({ chapterId: pick.chapterId, index: pick.from, block, kind });
  };

  const sendNote = async (body: string, proposed: string | null) => {
    if (!composing) return;
    const { chapterId, block, kind } = composing;
    const n = await post<DraftNote>(`/api/courses/${courseId}/chapters/${chapterId}/notes`, { kind, start: block.start, end: block.end, src: block.src ?? "", body, proposed });
    saved(n);
    setComposing(null);
    toast(kind === "suggestion" ? "Suggerimento inviato a chi scrive la materia" : "Commento inviato a chi scrive la materia");
  };

  const act = (a: SelectionAction) => {
    if (!sel) return;
    setPick(null);
    onSelectionAction(a, sel);
  };

  return (
    <div className="doc-scroll" ref={scroller}>
      <article
        className={`doc${still ? " readonly" : ""}`}
        ref={article}
        data-testid="doc-preview"
        aria-label="Bozza del documento"
        aria-busy={loading}
        onClick={(e) => {
          if (!(e.target instanceof Element) || e.target.closest(".doc-edit, .doc-notes")) return;
          if (e.target.closest(".doc-note-mark")) return markAt(e.target);
          if (!still) pickAt(e.target, e.shiftKey && !readOnly);
        }}
        onDoubleClick={(e) => !readOnly && editAt(e.target)}
        onKeyDown={(e) => {
          if (still) return;
          if (!(e.target instanceof HTMLElement && e.target.classList.contains("doc-block"))) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            pickAt(e.target, e.shiftKey && !readOnly);
          } else if (e.key === "F2" && !readOnly) {
            e.preventDefault();
            editAt(e.target);
          }
        }}
      >
        {error && !chapters && (
          <div className="alert danger">
            <Icon name="alert-circle" />
            <span>{error}</span>
          </div>
        )}
        {chapters === null && !error && (
          <div className="doc-loading muted" role="status">
            <Icon name="loader" className="spin" />
            Preparo la bozza…
          </div>
        )}
        {chapters && (chapters.length === 0 || chapters.every((c) => isHollow(c))) && empty}
        {chapters?.map((c, i) => (
          <section key={c.chapter.id} className="doc-ch" data-chapter-id={c.chapter.id} aria-label={c.chapter.title}>
            {c.blocks?.[0]?.heading !== "chapter" ? (
              <header className="doc-ch-head">
                <span className="doc-ch-n">Capitolo {i + 1}</span>
                <h1 className="doc-ch-title">{c.chapter.title}</h1>
              </header>
            ) : (
              <h1 className="sr-only">{c.chapter.title}</h1> // LaTeX draws the title: this one is for screen readers
            )}
            {c.error && (
              <div className="alert danger">
                <Icon name="alert-circle" />
                <span>{c.error}</span>
              </div>
            )}
            {c.blocks === null ? (
              <div className="doc-loading muted" role="status">
                <Icon name="loader" className="spin" />
                Composizione LaTeX…
              </div>
            ) : (
              c.blocks.map((b, k) => {
                if (editing && editing.chapterId === c.chapter.id && editing.index === k)
                  return <BlockEditor key={`edit:${b.start}`} courseId={courseId} edit={editing} onDone={edited} />;
                const here = placed.get(c.chapter.id)?.onBlock.get(k);
                const open = !!here && shown?.chapterId === c.chapter.id && shown.index === k;
                return (
                  <Fragment key={`${b.start}:${b.pages[0]?.url ?? b.error ?? ""}`}>
                    <Block b={b} index={k} width={c.width} picked={(!!pick && pick.chapterId === c.chapter.id && k >= pick.from && k <= pick.to) || composing?.chapterId === c.chapter.id && composing.index === k} still={still} notes={here?.length} notesOpen={open} />
                    {composing && composing.chapterId === c.chapter.id && composing.index === k && composing.block.start === b.start && (
                      <NoteComposer kind={composing.kind} src={b.src ?? ""} onSend={sendNote} onCancel={() => setComposing(null)} />
                    )}
                    {open && here && (
                      <NoteList notes={here} title="Le tue note su questo blocco" onSaved={saved} onRemoved={removed} onClose={() => setShown(null)} />
                    )}
                  </Fragment>
                );
              })
            )}
            {!!placed.get(c.chapter.id)?.apart.length && (
              <NoteList notes={placed.get(c.chapter.id)!.apart} title="Le tue note su testo cambiato" withSrc onSaved={saved} onRemoved={removed} />
            )}
            {isHollow(c) && <p className="doc-ch-empty muted">{readOnly ? "Questo capitolo è ancora vuoto." : "Questo capitolo è ancora vuoto. Chiedi all’assistente di scriverlo."}</p>}
          </section>
        ))}
      </article>
      {sel && !readOnly && <SelectionToolbar sel={sel} onAction={act} />}
      {sel && noting && <SelectionToolbar sel={sel} actions={NOTE_ACTIONS} label="Lascia una nota su questo blocco" onAction={noteAct} />}
    </div>
  );
}
