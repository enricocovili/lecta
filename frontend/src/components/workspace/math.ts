// KaTeX, bundled (CSS and fonts come through the build: nothing is loaded from another origin).
import katex from "katex";
import "katex/dist/katex.min.css";

function texToHtml(tex: string, display = false): string {
  try {
    return katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: "ignore", output: "htmlAndMathml" });
  } catch {
    return "";
  }
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
