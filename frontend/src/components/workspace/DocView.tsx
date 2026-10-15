// The draft of the whole document: every chapter typeset by LaTeX, block by block (paragraphs, headings, environments
// as SVG pictures), in one continuous scroll. A click picks a block (Shift+click extends the pick) and opens the
// toolbar to ask the AI about it; a double click (or F2) opens the block's text in place, to write anything there.
// Blocks are flashed where the AI just worked. Someone reading another's published course picks a block to comment
// on it or suggest a change (decision 129); a marker on a block shows the notes they left there. Whoever may change the
// course finds the readers' open notes the same way, and in a list per chapter, to accept, reject or resolve (130).
import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { get, post, put } from "../../lib/api";
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

/** Events that mean the reader scrolls by themselves. */
const TAKE_OVER = ["wheel", "touchstart", "pointerdown", "keydown"] as const;

export interface ScrollRequest {
  nonce: number;
  chapterId: number;
  sectionId?: string;
}

const NOTE_ACTIONS: ToolbarAction<NoteKind>[] = [
  { key: "comment", label: "Commenta", title: "Scrivi un commento su questo blocco: lo legge chi scrive la materia" },
  { key: "suggestion", label: "Suggerisci modifica", title: "Riscrivi il testo di questo blocco: chi scrive la materia accetta o rifiuta la modifica" },
];

/** Space a picture keeps around the ink, in bp (as the backend crops it). */
const PAD = 1.5;

/** Where each picture of a block goes, in % of the text width: as typeset, or shrunk as a whole so that a block that
 * runs past the margin (an overfull line) still fits the page. */
function layout(pages: DraftBlock["pages"], width: number): { width: string; marginLeft: string }[] {
  const x0 = Math.min(...pages.map((p) => p.x));
  const x1 = Math.max(...pages.map((p) => p.x + p.w));
  const fit = Math.min(1, (width + 2 * PAD) / (x1 - x0));
  const shift = fit < 1 ? -PAD - x0 * fit : 0;
  return pages.map((p) => ({ width: `${((p.w * fit) / width) * 100}%`, marginLeft: `${((p.x * fit + shift) / width) * 100}%` }));
}

const Block = memo(function Block({
  b,
  index,
  width,
  picked,
  still,
  notes = 0,
  notesOpen = false,
  theirs = false,
  writer = false,
}: {
  b: DraftBlock;
  index: number;
  width: number;
  picked: boolean;
  still: boolean;
  /** How many notes are on it (a reader's own, or the readers' open ones): a marker that shows them. */
  notes?: number;
  notesOpen?: boolean;
  /** The notes are the readers', shown to whoever writes the course. */
  theirs?: boolean;
  /** Whoever may change the course: told when the block runs past the margin. */
  writer?: boolean;
}) {
  const failed = !!b.error;
  const boxes = b.pages.length ? layout(b.pages, width) : [];
  return (
    <div
      className={`doc-block${b.heading ? " doc-heading" : ""}${picked ? " picked" : ""}${failed ? " failed" : ""}${notes ? " noted" : ""}`}
      data-line={b.start}
      data-end={b.end}
      data-index={index}
      id={b.id}
      tabIndex={still ? undefined : b.pages.length || failed ? 0 : -1}
    >
      {b.pages.map((p, k) => (
        <img
          key={p.url}
          src={p.url}
          alt={(b.src ?? "").slice(0, 400)}
          className={p.raster ? "raster" : undefined}
          draggable={false}
          loading="lazy"
          decoding="async"
          style={{ ...boxes[k], aspectRatio: `${p.w} / ${p.h}` }}
        />
      ))}
      {writer && b.overflow_mm && (
        <div className="doc-block-overflow" data-testid="block-overflow" title="Nel PDF questa parte supera il margine destro: qui è rimpicciolita per stare nella pagina. Chiedi all’assistente di sistemarla, o modificala con un doppio clic.">
          <Icon name="alert-triangle" /> Esce dal margine di {b.overflow_mm.toLocaleString("it-IT")} mm
        </div>
      )}
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
          aria-label={theirs ? (notes === 1 ? "Una nota aperta su questo blocco" : `${notes} note aperte su questo blocco`) : notes === 1 ? "Una tua nota su questo blocco" : `${notes} tue note su questo blocco`}
          title={theirs ? "Le note dei lettori su questo blocco" : "Le tue note su questo blocco"}
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
  revision = 0,
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
  /** Show the notes on the blocks: a reader's own, or (not read-only) the readers' open ones to close. */
  notes?: boolean;
  /** Read-only, and a click picks a block to comment on or suggest a change to (a published course). */
  canNote?: boolean;
  /** Bumped when the text was fetched again: the notes are placed anew. */
  revision?: number;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const article = useRef<HTMLElement>(null);
  const [pick, setPick] = useState<BlockPick | null>(null);
  const [rect, setRect] = useState<DraftSelection["rect"] | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [composing, setComposing] = useState<(Editing & { kind: NoteKind }) | null>(null);
  const [shown, setShown] = useState<{ chapterId: number; index: number } | null>(null);
  const [listed, setListed] = useState<number | null>(null);
  const noting = readOnly && canNote;
  const still = readOnly && !noting;
  const writer = !readOnly;
  const { notes: all, saved, removed, reload } = useDraftNotes(courseId, showNotes, revision);
  // whoever writes the course deals with the open notes; the closed ones are their authors' to look back on
  const notes = useMemo(() => (writer ? all.filter((n) => n.status === "open") : all), [all, writer]);
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
  // Chapters are typeset one by one, so the ones above the target grow from a spinner to their pages while it is being
  // reached: the target is kept at the top until the reader takes over the scroll.
  useEffect(() => {
    const sc = scroller.current;
    const art = article.current;
    if (!scrollTo || !sc || !art) return;
    const find = () => {
      const sec = art.querySelector<HTMLElement>(`[data-chapter-id="${scrollTo.chapterId}"]`);
      if (!sec) return null;
      return (scrollTo.sectionId && sec.querySelector<HTMLElement>(`[id="${CSS.escape(scrollTo.sectionId)}"]`)) || sec;
    };
    const target = find();
    if (!target) return;
    target.scrollIntoView({ block: "start", behavior: "smooth" });
    const align = () => find()?.scrollIntoView({ block: "start", behavior: "auto" });
    const watch = new ResizeObserver(align);
    watch.observe(art);
    const stop = () => {
      watch.disconnect();
      window.clearTimeout(limit);
      for (const ev of TAKE_OVER) sc.removeEventListener(ev, stop);
    };
    const limit = window.setTimeout(stop, 30_000);
    for (const ev of TAKE_OVER) sc.addEventListener(ev, stop, { passive: true });
    return stop;
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

  // -- the notes, on their blocks
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

  // An accepted suggestion changed its chapter: typeset again. A note that couldn't be closed as shown: both again.
  const retypeset = async (chapterId: number) => onBlockWritten(await get<DraftChapter>(`/api/courses/${courseId}/chapters/${chapterId}/draft`));
  const accepted = (n: DraftNote) => retypeset(n.chapter_id);
  const stale = (n: DraftNote) => {
    reload();
    void retypeset(n.chapter_id).catch(() => undefined);
  };

  const act = (a: SelectionAction) => {
    if (!sel) return;
    setPick(null);
    onSelectionAction(a, sel);
  };

  // The chapter's open notes for whoever writes it, those on text that changed included: a line that opens the list.
  const chapterNotes = (chapterId: number) => {
    const here = notes.filter((n) => n.chapter_id === chapterId);
    if (!here.length) return null;
    const changed = placed.get(chapterId)?.apart.length ?? 0;
    const open = listed === chapterId;
    return (
      <div className="doc-ch-notes">
        <button type="button" className="btn ghost sm" data-testid="chapter-notes" aria-expanded={open} onClick={() => setListed(open ? null : chapterId)}>
          <Icon name="message" />
          {here.length === 1 ? "1 nota aperta" : `${here.length} note aperte`}
          {changed > 0 && <span className="pill sm warn">{changed} su testo cambiato</span>}
          <Icon name="chevron-right" />
        </button>
        {open && (
          <NoteList
            notes={here}
            title="Note aperte su questo capitolo"
            testId="chapter-note-list"
            withSrc
            writer
            onSaved={saved}
            onRemoved={removed}
            onAccepted={accepted}
            onStale={stale}
            onClose={() => setListed(null)}
          />
        )}
      </div>
    );
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
          if (!(e.target instanceof Element) || e.target.closest(".doc-edit, .doc-notes, .doc-ch-notes")) return;
          if (e.target.closest(".doc-note-mark")) return markAt(e.target);
          if (!still) pickAt(e.target, e.shiftKey && !readOnly);
        }}
        onDoubleClick={(e) => !readOnly && !(e.target instanceof Element && e.target.closest(".doc-note-mark, .doc-notes")) && editAt(e.target)}
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
            {writer && c.blocks?.[0]?.heading !== "chapter" && chapterNotes(c.chapter.id)}
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
                    <Block b={b} index={k} width={c.width} picked={(!!pick && pick.chapterId === c.chapter.id && k >= pick.from && k <= pick.to) || composing?.chapterId === c.chapter.id && composing.index === k} still={still} notes={here?.length} notesOpen={open} theirs={writer} writer={writer} />
                    {writer && k === 0 && b.heading === "chapter" && chapterNotes(c.chapter.id)}
                    {composing && composing.chapterId === c.chapter.id && composing.index === k && composing.block.start === b.start && (
                      <NoteComposer kind={composing.kind} src={b.src ?? ""} onSend={sendNote} onCancel={() => setComposing(null)} />
                    )}
                    {open && here && (
                      <NoteList
                        notes={here}
                        title={writer ? "Note dei lettori su questo blocco" : "Le tue note su questo blocco"}
                        writer={writer}
                        onSaved={saved}
                        onRemoved={removed}
                        onAccepted={accepted}
                        onStale={stale}
                        onClose={() => setShown(null)}
                      />
                    )}
                  </Fragment>
                );
              })
            )}
            {!writer && !!placed.get(c.chapter.id)?.apart.length && (
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
