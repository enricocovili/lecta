// KaTeX, bundled (CSS and fonts come through the build: nothing is loaded from another origin).
import katex from "katex";
import "katex/dist/katex.min.css";

/** TeX inside `\(…\)` / `\[…\]` / `$…$` delimiters, without them. */
export function stripDelimiters(s: string): string {
  const t = s.trim();
  const m = /^\\\(([\s\S]*)\\\)$/.exec(t) ?? /^\\\[([\s\S]*)\\\]$/.exec(t) ?? /^\$\$([\s\S]*)\$\$$/.exec(t) ?? /^\$([\s\S]*)\$$/.exec(t);
  return (m ? m[1] : t).trim();
}

export function texToHtml(tex: string, display = false): string {
  try {
    return katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: "ignore", output: "htmlAndMathml" });
  } catch {
    return "";
  }
}

/** Render every `.math` span under `root` once, keeping its TeX in `data-tex`. */
export function renderMath(root: HTMLElement): void {
  root.querySelectorAll<HTMLElement>(".math").forEach((el) => {
    if (el.dataset.rendered === "1") return;
    const tex = el.dataset.tex ?? stripDelimiters(el.textContent ?? "");
    el.dataset.tex = tex;
    const display = el.classList.contains("display");
    const html = texToHtml(tex, display);
    if (html) {
      el.innerHTML = html;
      el.dataset.rendered = "1";
    }
  });
}

const cache = new Map<string, string>();

/** Same as texToHtml, memoised: the chat re-renders its formulas on every streamed token. */
export function texToHtmlCached(tex: string, display = false): string {
  const key = (display ? "D" : "I") + tex;
  let html = cache.get(key);
  if (html === undefined) {
    html = texToHtml(tex, display);
    if (cache.size > 800) cache.clear();
    cache.set(key, html);
  }
  return html;
}
