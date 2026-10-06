// The Markdown notes of one page: a plain text field that helps with lists (Enter continues them, Tab indents)
// and grows with what is written, so a slide can have as much room as it needs.
import { useEffect, useLayoutEffect, useRef } from "react";

const LIST = /^(\s*)([-*+]|\d+[.)])(\s+)(\[[ xX]\]\s+)?/;

function insert(el: HTMLTextAreaElement, text: string): void {
  el.focus();
  // execCommand keeps the browser's own undo history; the fallback is for browsers that dropped it.
  if (text === "" && el.selectionStart !== el.selectionEnd && document.execCommand("delete")) return;
  if (!document.execCommand("insertText", false, text)) {
    const { selectionStart: a, selectionEnd: b } = el;
    el.setRangeText(text, a, b, "end");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

function lineBounds(v: string, pos: number): [number, number] {
  const start = v.lastIndexOf("\n", pos - 1) + 1;
  const end = v.indexOf("\n", pos);
  return [start, end === -1 ? v.length : end];
}

export default function NotesField({
  value,
  onChange,
  minHeight,
  width,
  placeholder,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  minHeight: number;
  /** the width of the page: the text wraps again when it changes (a zoom) */
  width?: number;
  placeholder: string;
  label: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fit = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.max(el.scrollHeight + 2, minHeight)}px`;
  };
  useLayoutEffect(fit, [value, minHeight, width]);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [minHeight]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget;
    const v = el.value;
    const { selectionStart: a, selectionEnd: b } = el;
    if (e.nativeEvent.isComposing) return;
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && a === b) {
      const [s, end] = lineBounds(v, a);
      const line = v.slice(s, end);
      const m = LIST.exec(line);
      if (!m || a < s + m[0].length) return;
      e.preventDefault();
      if (line.slice(m[0].length).trim() === "") {
        // An empty item ends the list.
        el.setSelectionRange(s, end);
        insert(el, "");
        return;
      }
      const marker = /^\d/.test(m[2]) ? `${parseInt(m[2], 10) + 1}${m[2].slice(-1)}` : m[2];
      insert(el, `\n${m[1]}${marker}${m[3]}${m[4] ? "[ ] " : ""}`);
    } else if (e.key === "Tab" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const [s] = lineBounds(v, a);
      const [, end] = lineBounds(v, Math.max(a, b - (b > a && v[b - 1] === "\n" ? 1 : 0)));
      const block = v.slice(s, end);
      const multi = block.includes("\n");
      if (!multi && !LIST.test(block)) return; // in ordinary text Tab keeps moving the focus
      e.preventDefault();
      const lines = block.split("\n").map((ln) => (e.shiftKey ? ln.replace(/^( {1,2}|\t)/, "") : ln.trim() === "" ? ln : "  " + ln));
      el.setSelectionRange(s, end);
      insert(el, lines.join("\n"));
      el.setSelectionRange(a === b ? Math.max(s, a + (lines[0].length - block.split("\n")[0].length)) : s, s + lines.join("\n").length);
    } else if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "b" || e.key === "i")) {
      e.preventDefault();
      const mark = e.key === "b" ? "**" : "*";
      const sel = v.slice(a, b);
      insert(el, `${mark}${sel}${mark}`);
      if (a === b) el.setSelectionRange(a + mark.length, a + mark.length);
    } else if (e.key === "Escape") {
      el.blur();
    }
  };

  return (
    <textarea
      ref={ref}
      className="les-notes-field"
      value={value}
      placeholder={placeholder}
      aria-label={label}
      spellCheck
      lang="it"
      style={{ minHeight }}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={onKeyDown}
    />
  );
}
