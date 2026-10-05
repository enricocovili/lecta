// A file of the lab as code: CodeMirror with line numbers and the colours of its language (found from the file name, loaded
// on demand). Read-only here: the text can be selected but not changed. Colours come from CSS variables (`--syn-*`), so the
// light and dark themes both work.
import { useEffect, useRef } from "react";
import { HighlightStyle, LanguageDescription, syntaxHighlighting, bracketMatching, foldGutter } from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, drawSelection, highlightActiveLine, highlightActiveLineGutter, highlightSpecialChars, lineNumbers } from "@codemirror/view";
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

/** Base extensions of every code view; `extra` adds what a caller needs (comment markers, editing…). */
export function baseExtensions(readOnly: boolean): Extension[] {
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
    EditorState.readOnly.of(readOnly),
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

export default function CodeView({ content, filename, extra = [] }: { content: string; filename: string; extra?: Extension[] }) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const lang = useRef(new Compartment());

  useEffect(() => {
    if (!host.current) return;
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({ doc: content, extensions: [baseExtensions(true), lang.current.of([]), ...extra] }),
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

  return <div className="lab-code" ref={host} data-testid="lab-code" />;
}
