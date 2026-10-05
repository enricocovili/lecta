// A file of the lab as code: CodeMirror with line numbers and the colours of its language (found from the file name, loaded
// on demand). Read-only unless `editable`; comments sit on lines and follow them through the edits. Colours come from CSS variables (`--syn-*`), so the
// light and dark themes both work.
import { useEffect, useRef } from "react";
import { HighlightStyle, LanguageDescription, syntaxHighlighting, bracketMatching, foldGutter } from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { Compartment, EditorState, RangeSet, RangeSetBuilder, StateEffect, StateField, type Extension, type Text, type Transaction } from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  EditorView,
  GutterMarker,
  type Tooltip,
  drawSelection,
  gutter,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  showTooltip,
} from "@codemirror/view";
import { tags as t } from "@lezer/highlight";

const highlight = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.moduleKeyword, t.operatorKeyword, t.definitionKeyword], color: "var(--syn-keyword)" },
  { tag: [t.string, t.special(t.string), t.regexp, t.character], color: "var(--syn-string)" },
  { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], color: "var(--syn-comment)", fontStyle: "italic" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "var(--syn-number)" },
  { tag: [t.typeName, t.className, t.namespace, t.standard(t.typeName)], color: "var(--syn-type)" },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.macroName], color: "var(--syn-function)" },
  { tag: [t.processingInstruction, t.meta, t.annotation], color: "var(--syn-meta)" },
  { tag: [t.propertyName, t.attributeName], color: "var(--syn-property)" },
  { tag: [t.tagName, t.heading], color: "var(--syn-keyword)", fontWeight: "600" },
  { tag: t.invalid, color: "var(--danger-fg)" },
]);

const theme = EditorView.theme({
  "&": { height: "100%", fontSize: "13.5px", backgroundColor: "var(--paper-code)", color: "var(--fg)" },
  ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "1.55" },
  ".cm-gutters": { backgroundColor: "var(--bg-sunken)", color: "var(--fg-faint)", border: "none", borderRight: "1px solid var(--border)" },
  ".cm-activeLine": { backgroundColor: "var(--code-active)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--code-active)", color: "var(--fg-muted)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "var(--code-selection) !important" },
  "&.cm-focused": { outline: "none" },
  ".cm-content": { caretColor: "var(--fg)" },
});

/** Base extensions of every code view (read-only or not is up to the caller). */
export function baseExtensions(): Extension[] {
  return [
    lineNumbers(),
    foldGutter(),
    highlightSpecialChars(),
    drawSelection(),
    highlightActiveLine(),
    highlightActiveLineGutter(),
    bracketMatching(),
    syntaxHighlighting(highlight),
    theme,
    EditorState.tabSize.of(4),
  ];
}

/** The language of a file name, loaded (each language is its own chunk); null for plain text. */
export async function languageFor(filename: string): Promise<Extension | null> {
  const desc = LanguageDescription.matchFilename(languages, filename) ?? matchByExtension(filename);
  if (!desc) return null;
  try {
    return await desc.load();
  } catch {
    return null;
  }
}

// A few lab files CodeMirror's list names differently or not at all.
const ALIASES: Record<string, string> = { h: "C", ino: "C++", m: "Octave", ipynb: "JSON", v: "Verilog", sv: "SystemVerilog", asm: "Gas", s: "Gas" };

function matchByExtension(filename: string): LanguageDescription | null {
  const ext = filename.toLowerCase().split(".").pop() ?? "";
  const name = ALIASES[ext];
  return name ? LanguageDescription.matchLanguageName(languages, name, false) : null;
}

/** A comment's place in the code: lines `from`–`to` (1-based). */
export interface Mark {
  id: string;
  from: number;
  to: number;
}

export interface Lines {
  from: number;
  to: number;
  text: string;
}

/** Where a comment's lines went after an edit; `gone` when they were all removed. */
export interface Moved extends Lines {
  id: string;
  gone: boolean;
}

interface Hooks {
  onMark?: (id: string) => void;
  onComment?: (lines: Lines) => void;
  /** ask the assistant about the selected lines */
  onAsk?: (lines: Lines) => void;
  onChange?: (doc: string) => void;
  onMoved?: (moved: Moved[]) => void;
}

const setMarks = StateEffect.define<{ marks: Mark[]; active: string | null }>();

class CommentMarker extends GutterMarker {
  constructor(readonly ids: string[], readonly on: boolean) {
    super();
  }
  eq(other: CommentMarker) {
    return other.ids.join() === this.ids.join() && other.on === this.on;
  }
  toDOM() {
    const el = document.createElement("span");
    el.className = `cm-lab-dot${this.on ? " on" : ""}`;
    el.textContent = this.ids.length > 1 ? String(this.ids.length) : "";
    el.title = this.ids.length > 1 ? `${this.ids.length} commenti` : "Commento";
    return el;
  }
}

interface MarkState {
  lines: DecorationSet;
  gutter: RangeSet<GutterMarker>;
  marks: Mark[];
  active: string | null;
  /** what the last edit moved */
  moved: Moved[];
}

const clampLine = (doc: Text, n: number) => Math.min(Math.max(1, n), doc.lines);

function build(doc: Text, marks: Mark[], active: string | null, moved: Moved[] = []): MarkState {
  const lines = new RangeSetBuilder<Decoration>();
  const per = new Map<number, string[]>();
  const cls = new Map<number, string>();
  for (const m of marks) {
    const a = clampLine(doc, m.from);
    const b = Math.max(a, clampLine(doc, m.to));
    per.set(a, [...(per.get(a) ?? []), m.id]);
    for (let n = a; n <= b; n++) if (cls.get(n) !== "on") cls.set(n, m.id === active ? "on" : "mark");
  }
  for (const n of [...cls.keys()].sort((x, y) => x - y)) {
    lines.add(doc.line(n).from, doc.line(n).from, Decoration.line({ class: cls.get(n) === "on" ? "cm-lab-mark cm-lab-on" : "cm-lab-mark" }));
  }
  const gutter = new RangeSetBuilder<GutterMarker>();
  for (const n of [...per.keys()].sort((x, y) => x - y)) {
    const ids = per.get(n)!;
    gutter.add(doc.line(n).from, doc.line(n).from, new CommentMarker(ids, active !== null && ids.includes(active)));
  }
  return { lines: lines.finish(), gutter: gutter.finish(), marks, active, moved };
}

/** The marks after an edit: each follows its lines; one whose lines were all deleted is gone. */
function follow(marks: Mark[], tr: Transaction): { marks: Mark[]; moved: Moved[] } {
  const before = tr.startState.doc;
  const after = tr.state.doc;
  const kept: Mark[] = [];
  const moved: Moved[] = [];
  for (const m of marks) {
    const a = before.line(clampLine(before, m.from)).from;
    const b = before.line(clampLine(before, m.to)).to;
    let gone = false;
    tr.changes.iterChangedRanges((fromA, toA) => {
      if (toA > fromA && fromA <= a && toA >= b) gone = true;
    });
    const na = tr.changes.mapPos(a, 1);
    const nb = Math.max(na, tr.changes.mapPos(b, -1));
    const from = after.lineAt(na).number;
    const to = after.lineAt(nb).number;
    if (gone) {
      moved.push({ id: m.id, from: m.from, to: m.to, text: "", gone: true });
      continue;
    }
    kept.push({ id: m.id, from, to });
    const text = after.sliceString(after.line(from).from, after.line(to).to);
    if (from !== m.from || to !== m.to || tr.changes.touchesRange(a, b)) moved.push({ id: m.id, from, to, text, gone: false });
  }
  return { marks: kept, moved };
}

/** The lines a selection covers (a selection ending at the start of a line doesn't take that line). */
export function selectedLines(state: EditorState): Lines {
  const sel = state.selection.main;
  const doc = state.doc;
  const first = doc.lineAt(sel.from);
  let last = doc.lineAt(sel.to);
  if (!sel.empty && sel.to === last.from && last.number > first.number) last = doc.line(last.number - 1);
  return { from: first.number, to: last.number, text: doc.sliceString(first.from, last.to) };
}

/** Marked lines (following the edits), the dots in their gutter, Ctrl+Alt+M. */
function comments(hooks: { current: Hooks }, canComment: boolean): Extension[] {
  const field = StateField.define<MarkState>({
    create: (state) => build(state.doc, [], null),
    update(value, tr) {
      for (const e of tr.effects) if (e.is(setMarks)) return build(tr.state.doc, e.value.marks, e.value.active);
      if (!tr.docChanged) return value.moved.length ? { ...value, moved: [] } : value;
      const { marks, moved } = follow(value.marks, tr);
      return build(tr.state.doc, marks, value.active, moved);
    },
    provide: (f) => EditorView.decorations.from(f, (v) => v.lines),
  });
  const marksGutter = gutter({
    class: "cm-lab-gutter",
    markers: (view) => view.state.field(field).gutter,
    domEventHandlers: {
      mousedown(view, line) {
        const n = view.state.doc.lineAt(line.from).number;
        const hit = view.state.field(field).marks.find((m) => m.from <= n && n <= m.to);
        if (hit) hooks.current.onMark?.(hit.id);
        return !!hit;
      },
    },
  });
  const report = EditorView.updateListener.of((u) => {
    if (!u.docChanged) return;
    const moved = u.state.field(field).moved;
    if (moved.length) hooks.current.onMoved?.(moved);
    hooks.current.onChange?.(u.state.doc.toString());
  });
  const ask = (view: EditorView) => {
    hooks.current.onComment?.(selectedLines(view.state));
    return true;
  };
  return [field, marksGutter, report, ...(canComment ? [keymap.of([{ key: "Mod-Alt-m", run: ask }])] : [])];
}

/** The bubble over a selection: «Commenta» and «Chiedi» (only while reading: while editing a selection is for typing over). */
function askBubble(hooks: { current: Hooks }, canComment: boolean, canAsk: boolean): Extension {
  const button = (label: string, title: string, testid: string, run: () => void) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn sm cm-lab-ask";
    b.dataset.testid = testid;
    b.textContent = label;
    b.title = title;
    b.addEventListener("mousedown", (e) => e.preventDefault());
    b.addEventListener("click", run);
    return b;
  };
  return StateField.define<Tooltip | null>({
    create: () => null,
    update(_, tr) {
      const sel = tr.state.selection.main;
      if (sel.empty) return null;
      return {
        pos: sel.head,
        above: sel.head < sel.anchor,
        strictSide: true,
        arrow: false,
        create: (view) => {
          const dom = document.createElement("div");
          dom.className = "cm-lab-bubble";
          if (canComment) dom.append(button("Commenta", "Commenta le righe selezionate (Ctrl+Alt+M)", "lab-comment-selection", () => hooks.current.onComment?.(selectedLines(view.state))));
          if (canAsk) dom.append(button("Chiedi", "Chiedi all’assistente di queste righe", "lab-ask-selection", () => hooks.current.onAsk?.(selectedLines(view.state))));
          return { dom };
        },
      };
    },
    provide: (f) => showTooltip.from(f),
  });
}

function editing(on: boolean, hooks: { current: Hooks }, canComment: boolean, canAsk: boolean): Extension {
  if (on) return [EditorState.readOnly.of(false), history(), keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab])];
  return [EditorState.readOnly.of(true), canComment || canAsk ? askBubble(hooks, canComment, canAsk) : []];
}

export default function CodeView({
  content,
  filename,
  marks = [],
  active = null,
  reveal = null,
  editable = false,
  onMark,
  onComment,
  onAsk,
  onChange,
  onMoved,
}: {
  /** the text the view starts from: a new value is a new view (edits made here don't come back through it) */
  content: string;
  filename: string;
  marks?: Mark[];
  active?: string | null;
  /** scroll to this line (a new object each time) */
  reveal?: { line: number } | null;
  editable?: boolean;
} & Hooks) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const lang = useRef(new Compartment());
  const edit = useRef(new Compartment());
  const hooks = useRef<Hooks>({ onMark, onComment, onAsk, onChange, onMoved });
  hooks.current = { onMark, onComment, onAsk, onChange, onMoved };

  useEffect(() => {
    if (!host.current) return;
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: content,
        extensions: [baseExtensions(), lang.current.of([]), edit.current.of(editing(editable, hooks, !!onComment, !!onAsk)), comments(hooks, !!onComment)],
      }),
    });
    view.current = v;
    let alive = true;
    void languageFor(filename).then((l) => {
      if (alive && l) v.dispatch({ effects: lang.current.reconfigure(l) });
    });
    return () => {
      alive = false;
      v.destroy();
      view.current = null;
    };
    // A new file (or a new version of it) is a new view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content, filename]);

  useEffect(() => {
    const v = view.current;
    if (!v) return;
    v.dispatch({ effects: edit.current.reconfigure(editing(editable, hooks, !!onComment, !!onAsk)) });
    if (editable) v.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editable]);

  useEffect(() => {
    view.current?.dispatch({ effects: setMarks.of({ marks, active }) });
  }, [marks, active, content]);

  useEffect(() => {
    const v = view.current;
    if (!v || !reveal) return;
    const line = v.state.doc.line(clampLine(v.state.doc, reveal.line));
    v.dispatch({ effects: EditorView.scrollIntoView(line.from, { y: "center" }) });
  }, [reveal]);

  return <div className={`lab-code ${editable ? "editing" : ""}`} ref={host} data-testid="lab-code" />;
}
