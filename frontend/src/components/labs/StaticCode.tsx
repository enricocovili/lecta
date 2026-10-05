// A piece of code with the colours of its language, drawn once as plain spans (no editor): the cells of a notebook.
import { LanguageDescription } from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { highlightCode, tagHighlighter, tags as t } from "@lezer/highlight";
import { useEffect, useState, type ReactNode } from "react";

// The same colours as the code view (CodeView's HighlightStyle), as classes styled in labs.css.
const highlighter = tagHighlighter([
  { tag: [t.keyword, t.controlKeyword, t.moduleKeyword, t.operatorKeyword, t.definitionKeyword], class: "lt-keyword" },
  { tag: [t.string, t.special(t.string), t.regexp, t.character], class: "lt-string" },
  { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], class: "lt-comment" },
  { tag: [t.number, t.bool, t.null, t.atom], class: "lt-number" },
  { tag: [t.typeName, t.className, t.namespace, t.standard(t.typeName)], class: "lt-type" },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.macroName], class: "lt-function" },
  { tag: [t.processingInstruction, t.meta, t.annotation], class: "lt-meta" },
  { tag: [t.propertyName, t.attributeName], class: "lt-property" },
]);

export default function StaticCode({ code, language }: { code: string; language: string | null }) {
  const [nodes, setNodes] = useState<ReactNode[] | null>(null);
  useEffect(() => {
    let alive = true;
    setNodes(null);
    const desc = LanguageDescription.matchLanguageName(languages, language || "python", true);
    desc
      ?.load()
      .then((support) => {
        const out: ReactNode[] = [];
        let k = 0;
        highlightCode(
          code,
          support.language.parser.parse(code),
          highlighter,
          (text, classes) => out.push(classes ? <span key={k++} className={classes}>{text}</span> : text),
          () => out.push("\n"),
        );
        if (alive) setNodes(out);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [code, language]);
  return <pre className="lab-static">{nodes ?? code}</pre>;
}
