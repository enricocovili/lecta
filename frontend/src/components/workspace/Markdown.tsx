// A small, safe markdown renderer for the assistant's replies: it builds React elements (never raw HTML).
// Supports paragraphs, headings, lists, quotes, tables, code, bold/italic, links and $…$ maths (KaTeX).
import { Fragment, memo, type ReactNode } from "react";
import { texToHtmlCached } from "./math";

const INLINE =
  /(`[^`\n]+`)|(\$\$[^$]+?\$\$)|(\$(?![\s\d])[^$\n]+?(?<!\s)\$(?!\d))|(\\\((.+?)\\\))|(\*\*(.+?)\*\*)|(\[([^\]\n]+)\]\(([^)\s]+)\))|(\*(?![\s*])([^*\n]+?)\*)|((?<![\w])_(?!\s)([^_\n]+?)_(?![\w]))/g;

function safeHref(url: string): string | null {
  return /^(https?:\/\/|mailto:|\/(?!\/)|#)/i.test(url) ? url : null;
}

function Tex({ tex, display }: { tex: string; display?: boolean }) {
  const html = texToHtmlCached(tex.trim(), !!display);
  if (!html) return <code>{tex}</code>;
  return <span className={display ? "md-math display" : "md-math"} dangerouslySetInnerHTML={{ __html: html }} />;
}

function inline(text: string, keyPrefix = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  const re = new RegExp(INLINE.source, "g"); // nested calls (bold, links) must not share `lastIndex`
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = `${keyPrefix}${n++}`;
    if (m[1]) out.push(<code key={key}>{m[1].slice(1, -1)}</code>);
    else if (m[2]) out.push(<Tex key={key} tex={m[2].slice(2, -2)} display />);
    else if (m[3]) out.push(<Tex key={key} tex={m[3].slice(1, -1)} />);
    else if (m[4]) out.push(<Tex key={key} tex={m[5]} />);
    else if (m[6]) out.push(<strong key={key}>{inline(m[7], key)}</strong>);
    else if (m[8]) {
      const href = safeHref(m[10]);
      out.push(
        href ? (
          <a key={key} href={href} target={href.startsWith("#") ? undefined : "_blank"} rel="noopener noreferrer">
            {inline(m[9], key)}
          </a>
        ) : (
          m[9]
        ),
      );
    } else if (m[11]) out.push(<em key={key}>{inline(m[12], key)}</em>);
    else if (m[13]) out.push(<em key={key}>{inline(m[14], key)}</em>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const lines = (s: string) => s.split("\n").map((l, i, a) => (
  <Fragment key={i}>
    {inline(l, `l${i}`)}
    {i < a.length - 1 && <br />}
  </Fragment>
));

const LI = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const isBlockStart = (l: string) => /^\s*(```|#{1,6}\s|>|\$\$|\\\[)/.test(l) || LI.test(l) || /^\s*\|.*\|\s*$/.test(l) || /^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(l);

interface Item {
  indent: number;
  ordered: boolean;
  text: string;
  children: Item[];
}

function renderList(items: Item[], key: string): ReactNode {
  const ordered = items[0].ordered;
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag key={key}>
      {items.map((it, i) => (
        <li key={i}>
          {lines(it.text)}
          {it.children.length > 0 && renderList(it.children, `${key}-${i}`)}
        </li>
      ))}
    </Tag>
  );
}

function buildItems(flat: Item[]): Item[] {
  const root: Item[] = [];
  const stack: Item[] = [];
  for (const it of flat) {
    while (stack.length && stack[stack.length - 1].indent >= it.indent) stack.pop();
    if (stack.length) stack[stack.length - 1].children.push(it);
    else root.push(it);
    stack.push(it);
  }
  return root;
}

function blocks(src: string): ReactNode[] {
  const ls = src.replace(/\r\n?/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < ls.length) {
    const line = ls[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    // code fence (an unclosed one, while streaming, shows what has arrived so far)
    const fence = /^\s*```\s*([\w+-]*)/.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < ls.length && !/^\s*```\s*$/.test(ls[i])) body.push(ls[i++]);
      i++;
      out.push(
        <pre key={k++} className="md-code" data-lang={fence[1] || undefined}>
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    // display maths
    if (/^\s*(\$\$|\\\[)/.test(line)) {
      const close = line.trim().startsWith("$$") ? "$$" : "\\]";
      const open = close === "$$" ? "$$" : "\\[";
      let text = line.trim().slice(open.length);
      if (text.endsWith(close)) {
        text = text.slice(0, -close.length);
        i++;
      } else {
        const body = [text];
        i++;
        while (i < ls.length && !ls[i].trim().endsWith(close)) body.push(ls[i++]);
        if (i < ls.length) body.push(ls[i++].trim().slice(0, -close.length));
        text = body.join("\n");
      }
      out.push(<Tex key={k++} tex={text} display />);
      continue;
    }
    const h = /^\s*(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      out.push(
        <p key={k++} className="md-h">
          {inline(h[2])}
        </p>,
      );
      i++;
      continue;
    }
    if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) {
      out.push(<hr key={k++} />);
      i++;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      while (i < ls.length && /^\s*>/.test(ls[i])) body.push(ls[i++].replace(/^\s*>\s?/, ""));
      out.push(<blockquote key={k++}>{blocks(body.join("\n"))}</blockquote>);
      continue;
    }
    // table: header row + separator row
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < ls.length && /^\s*\|[\s:|-]+\|\s*$/.test(ls[i + 1])) {
      const cells = (l: string) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < ls.length && /^\s*\|.*\|\s*$/.test(ls[i])) rows.push(cells(ls[i++]));
      out.push(
        <div key={k++} className="md-table">
          <table>
            <thead>
              <tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, j) => (
                <tr key={j}>{r.map((c, x) => <td key={x}>{inline(c)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (LI.test(line)) {
      const flat: Item[] = [];
      while (i < ls.length) {
        const m = LI.exec(ls[i]);
        if (m) {
          flat.push({ indent: m[1].replace(/\t/g, "  ").length, ordered: /\d/.test(m[2]), text: m[3], children: [] });
          i++;
        } else if (ls[i].trim() && /^\s{2,}\S/.test(ls[i]) && flat.length && !isBlockStart(ls[i])) {
          flat[flat.length - 1].text += "\n" + ls[i++].trim();
        } else break;
      }
      out.push(renderList(buildItems(flat), `ul${k++}`));
      continue;
    }
    // paragraph
    const para: string[] = [line];
    i++;
    while (i < ls.length && ls[i].trim() && !isBlockStart(ls[i])) para.push(ls[i++]);
    out.push(<p key={k++}>{lines(para.join("\n"))}</p>);
  }
  return out;
}

/** Markdown → React. Memoised: only the text that changed is re-rendered. */
export const Markdown = memo(function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return <div className={`md ${className}`}>{blocks(text)}</div>;
});

/** Inline-only (chips, one-line labels). */
export function InlineMarkdown({ text }: { text: string }) {
  return <>{inline(text)}</>;
}
