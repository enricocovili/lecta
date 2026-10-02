"""Draft preview: a chapter's LaTeX → an HTML fragment in ~100 ms (pandoc), instead of a PDF build.

The fragment is what the workspace shows and what the user selects text in:

* every block carries `data-line` (the source line where it starts), so a selection maps back to the file;
* maths stays TeX (`<span class="math …" data-tex="…">`), rendered by KaTeX in the browser;
* theorem-like environments, review notes, pictures and TikZ placeholders get stable classes;
* the output is sanitised (allow-listed tags/attributes, safe URLs), whatever the model wrote.

Nothing here is authoritative: the PDF build stays the reference for layout.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from collections import OrderedDict
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

from . import latexmacros

THEOREM_ENVS = ("definition", "theorem", "lemma", "proposition", "corollary", "example", "remark")
KIND_NAMES = {
    "it": {"definition": "Definizione", "theorem": "Teorema", "lemma": "Lemma", "proposition": "Proposizione",
           "corollary": "Corollario", "example": "Esempio", "remark": "Osservazione", "proof": "Dimostrazione"},
    "en": {"definition": "Definition", "theorem": "Theorem", "lemma": "Lemma", "proposition": "Proposition",
           "corollary": "Corollary", "example": "Example", "remark": "Remark", "proof": "Proof"},
}
VERBATIM_ENVS = {"verbatim", "lstlisting", "minted", "Verbatim"}
PLACEHOLDER_ENVS = ("tikzpicture", "circuitikz", "tikzcd", "forest", "chemfigure")
PANDOC_TIMEOUT_S = 20
MAX_SOURCE = 600_000

_SECTION_RE = re.compile(r"^\\(chapter|section|subsection|subsubsection|paragraph)\*?\b")
_BEGIN_END_RE = re.compile(r"\\(begin|end)\{([A-Za-z*]+)\}")
_CONTAINERS = set(THEOREM_ENVS) | {"proof", "appproof"}


class PreviewError(Exception):
    pass


# --------------------------------------------------------------------------- picture sizes

# Where a picture may go in the PDF: (width, height) as percent of the line width and of the text height. The course's own
# preamble defines the commands, so its numbers are the ones to use; these are the defaults of the template.
DEFAULT_LIMITS = {"lectaimage": (50, 22), "lectaimagewithtext": (39, 27), "lectaimagepair": (48, 32)}
_MAX_W = re.compile(r"max width\s*=\s*([0-9.]+)\s*\\(?:linewidth|textwidth)")
_MAX_H = re.compile(r"max height\s*=\s*([0-9.]+)\s*\\textheight")
_BOX_W = re.compile(r"\\begin\{minipage\}\[[a-z]\]\{([0-9.]+)\\linewidth\}")


def image_limits(preamble: str) -> dict[str, tuple[int, int]]:
    """How big the picture commands let a picture be in the course's preamble: name → (width %, height %)."""
    out = dict(DEFAULT_LIMITS)
    for name in DEFAULT_LIMITS:
        m = re.search(r"\\(?:re)?newcommand\{\\" + name + r"\}", preamble)
        if not m:
            continue
        nxt = re.search(r"\\(?:re|provide)?newcommand|\\@ifundefined", preamble[m.end():])
        body = preamble[m.end(): m.end() + (nxt.start() if nxt else 2000)]
        w, h = _MAX_W.search(body), _MAX_H.search(body)
        box = _BOX_W.search(body)
        if w or h or box:
            width = float(w.group(1)) if w else 1.0
            if box and name != "lectaimage":
                width *= float(box.group(1))
            out[name] = (max(5, min(100, round(width * 100))), max(5, min(100, round(float(h.group(1)) * 100))) if h else DEFAULT_LIMITS[name][1])
    return out


# --------------------------------------------------------------------------- LaTeX preparation


# Brace matching and macro parsing live in latexmacros (shared with the import).
_match_brace = latexmacros.match_brace
_macro_spans = latexmacros.macro_spans
_replace_macro = latexmacros.replace_macro


def annotate_lines(src: str) -> str:
    """Put a `\\hypertarget{sl-N}{}` paragraph before every block that starts on source line N
    (top level and inside theorem-like boxes). It doesn't change what is typeset."""
    out: list[str] = []
    stack: list[str] = []
    verb: str | None = None
    prev_blank = True
    prev_heading = False
    for i, line in enumerate(src.split("\n"), start=1):
        s = line.strip()
        if verb is not None:
            out.append(line)
            for m in _BEGIN_END_RE.finditer(line):
                if m.group(1) == "end" and m.group(2) == verb:
                    verb = None
                    if stack:
                        stack.pop()
            continue
        blank = not s
        starts = not blank and not s.startswith("%") and (prev_blank or prev_heading or bool(_SECTION_RE.match(s)) or s.startswith(("\\begin{", "\\[")))
        if starts and all(n in _CONTAINERS for n in stack):
            out.append(f"\n\\hypertarget{{sl-{i}}}{{}}\n")
        out.append(line)
        for m in _BEGIN_END_RE.finditer(line):
            if m.group(1) == "begin":
                stack.append(m.group(2))
                if m.group(2) in VERBATIM_ENVS:
                    verb = m.group(2)
            elif stack:
                stack.pop()
        prev_blank = blank
        prev_heading = bool(_SECTION_RE.match(s))
    return "\n".join(out)


def prepare_latex(src: str, files: set[str], limits: dict[str, tuple[int, int]] | None = None) -> tuple[str, list[str]]:
    """The chapter source as pandoc should see it, and warnings about what the preview can't show."""
    warnings: list[str] = []
    s = annotate_lines(src)

    # Theorem-like boxes: keep the optional title (pandoc drops it) and give proofs a neutral name.
    def thm(m: re.Match) -> str:
        env, title = m.group(1), m.group(2)
        head = f"\\begin{{{env}}}\n\n"
        return head + (f"\\hypertarget{{thmtitle}}{{{title}}}\n\n" if title and title.strip() else "")

    s = re.sub(r"\\begin\{(" + "|".join(THEOREM_ENVS) + r")\}[ \t]*(?:\[([^\]\n]*)\])?", thm, s)
    s = s.replace("\\begin{proof}", "\\begin{appproof}\n\n").replace("\\end{proof}", "\\end{appproof}")

    def review(_opt, args) -> str:
        return "\n\\begin{appreview}\n" + args[0] + "\n\\end{appreview}\n"

    s = _replace_macro(s, "review", 1, review)

    def picture(path: str, cap: str) -> str:
        """One picture as a figure (with its caption), or a placeholder when the file isn't there."""
        if not path:
            return ""
        if path not in files:
            warnings.append(f"Immagine non trovata: {path}")
            return f"\\begin{{appplaceholder}}Immagine mancante: {path.rsplit('/', 1)[-1]}\\end{{appplaceholder}}\n\n"
        caption = f"\\caption{{{cap.strip()}}}" if cap.strip() else ""
        return f"\\begin{{figure}}\\includegraphics{{{path}}}{caption}\\end{{figure}}\n\n"

    # The three picture commands. Each becomes a box (app<kind>W<width>H<height>) holding the figures, the sizes being the
    # limits of the course's own definitions of the commands (the PDF is typeset with them); the CSS lays the box out.
    out, pos = [], 0
    for start, end, name, opt, args in latexmacros.image_spans(s):
        if start < pos:
            continue
        w, h = (limits or {}).get(name, DEFAULT_LIMITS[name])
        if name == "lectaimage":
            box, inner = "appimg", picture(args[0].strip(), opt or "")
        elif name == "lectaimagewithtext":
            box, inner = "appside", picture(args[0].strip(), opt or "") + args[1].strip() + "\n\n"
        else:
            box, inner = "apppair", picture(args[1].strip(), args[0]) + picture(args[3].strip(), args[2])
        out.append(s[pos:start] + f"\n\\begin{{{box}W{w}H{h}}}\n\n{inner}\\end{{{box}W{w}H{h}}}\n")
        pos = end
    out.append(s[pos:])
    s = "".join(out)

    def figure(opt, args) -> str:
        name = args[0].strip()
        warnings.append(f"Disegno «{name}»: visibile nel PDF")
        return f"\n\\begin{{appplaceholder}}Disegno «{name}» (visibile nel PDF)\\end{{appplaceholder}}\n"

    s = _replace_macro(s, "lectafigure", 1, figure)

    n_drawings = 0
    for env in PLACEHOLDER_ENVS:
        pat = re.compile(r"\\begin\{" + env + r"\}(?:\[[^\]]*\])?.*?\\end\{" + env + r"\}", re.S)
        s, n = pat.subn("\n\\\\begin{appplaceholder}Disegno (visibile nel PDF)\\\\end{appplaceholder}\n", s)
        n_drawings += n
    if n_drawings:
        warnings.append(f"{n_drawings} disegno/i TikZ: visibili solo nel PDF")
    # pgfplots axes outside a tikzpicture, and other environments pandoc can't render.
    s = re.sub(r"\\(newpage|clearpage|pagebreak|vspace\*?\{[^}]*\}|hspace\*?\{[^}]*\}|noindent|centering)\b", "", s)
    return s, warnings


# --------------------------------------------------------------------------- pandoc


async def run_pandoc(latex: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "pandoc", "-f", "latex", "-t", "html", "--mathjax", "--wrap=none", "--no-highlight", "--sandbox",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(latex.encode()), PANDOC_TIMEOUT_S)
    except TimeoutError as e:
        proc.kill()
        raise PreviewError("l'anteprima ha impiegato troppo tempo") from e
    if proc.returncode != 0:
        raise PreviewError("pandoc: " + err.decode("utf-8", "replace")[:300])
    return out.decode("utf-8")


# --------------------------------------------------------------------------- HTML post-processing

_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "col", "colgroup", "dd", "del", "div", "dl", "dt", "em",
    "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "mark", "ol", "p", "pre",
    "q", "s", "samp", "section", "small", "span", "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr",
    "u", "ul", "var",
}
_VOID = {"br", "col", "hr", "img"}
_GLOBAL_ATTRS = {"class", "id", "data-line", "data-tex", "title", "lang"}
_TAG_ATTRS = {"a": {"href"}, "img": {"src", "alt", "width", "height"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"},
              "ol": {"start", "type"}}
_BOX_CLASS = re.compile(r"app(img|side|pair)W(\d{1,3})H(\d{1,3})")
_SAFE_HREF = re.compile(r"^(https?://|mailto:|#)", re.I)
_ALIGN = re.compile(r"text-align:\s*(left|right|center|justify)", re.I)
_MATH_CLEAN = re.compile(r"\\(label|nonumber|notag)\b(\{[^}]*\})?")


def _tex_of(math_html: str) -> str:
    tex = html.unescape(re.sub(r"<[^>]+>", "", math_html)).strip()
    for a, b in (("\\(", "\\)"), ("\\[", "\\]")):
        if tex.startswith(a) and tex.endswith(b):
            tex = tex[2:-2]
    return _MATH_CLEAN.sub("", tex).strip()


class _Post(HTMLParser):
    def __init__(self, course_id: int, chapter_id: int, kinds: dict[str, str]):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.cid, self.chid, self.kinds = course_id, chapter_id, kinds
        self.pending_line: int | None = None
        self.closers: list[str] = []
        self.math_start: int | None = None
        self.math_depth = 0
        self.toc: list[dict[str, Any]] = []
        self.heading: dict[str, Any] | None = None
        self.n_heading = 0
        self._suppress = False

    # ---- helpers
    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]], extra: dict[str, str]) -> str:
        allowed = _GLOBAL_ATTRS | _TAG_ATTRS.get(tag, set())
        kept: dict[str, str] = {}
        for k, v in attrs:
            v = v or ""
            if k == "style":
                if tag in ("td", "th") and (m := _ALIGN.search(v)):
                    kept["style"] = f"text-align:{m.group(1).lower()}"
                continue
            if k not in allowed:
                continue
            if k == "href" and not _SAFE_HREF.match(v.strip()):
                continue
            if k == "src":
                if not v.startswith("/api/courses/"):
                    continue
            kept[k] = v
        kept.update(extra)
        return "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in kept.items())

    def _emit(self, s: str) -> None:
        self.out.append(s)

    # ---- parser callbacks
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict((k, v or "") for k, v in attrs)
        classes = (a.get("class") or "").split()
        # Line markers: an empty <div id="sl-N"> tells us where the next block starts.
        if tag == "div" and re.fullmatch(r"sl-\d+", a.get("id", "")):
            self.pending_line = int(a["id"][3:])
            self.closers.append("")  # swallowed together with its end tag
            self._suppress = True
            return
        if tag in _VOID:
            self._emit(f"<{tag}{self._attrs(tag, attrs, {'loading': 'lazy'} if tag == 'img' else {})}>")
            self.pending_line = None if tag == "hr" else self.pending_line
            return
        if tag not in _ALLOWED_TAGS:
            self.closers.append("")
            return
        extra: dict[str, str] = {}
        closing = f"</{tag}>"
        if self.pending_line is not None and tag not in ("span", "em", "strong", "a", "sub", "sup", "code"):
            extra["data-line"] = str(self.pending_line)
            line, self.pending_line = self.pending_line, None
        else:
            line = None
        # Roles by class.
        if tag == "div":
            env = next((c for c in classes if c in THEOREM_ENVS or c == "appproof"), None)
            if env:
                kind = "proof" if env == "appproof" else env
                attrs = [(k, v) for k, v in attrs if k != "class"]
                extra["class"] = f"thm thm-{kind}"
            elif (box := next((_BOX_CLASS.fullmatch(c) for c in classes if _BOX_CLASS.fullmatch(c)), None)) is not None:
                attrs = [(k, v) for k, v in attrs if k != "class"]
                extra["class"] = {"img": "img-box", "side": "side-fig", "pair": "pair-fig"}[box.group(1)]
                extra["style"] = f"--mw:{int(box.group(2))}%;--mh:{int(box.group(3)) / 100}"
            elif "appreview" in classes:
                attrs = [(k, v) for k, v in attrs if k != "class"]
                extra["class"] = "review-note"
            elif "appplaceholder" in classes:
                self._emit(f'<figure class="placeholder"{self._attrs("figure", [], extra)}><div>')
                self.closers.append("</div></figure>")
                return
        if tag == "span" and "math" in classes:
            self.math_start = len(self.out)
            self.math_depth = 1
        elif self.math_start is not None and tag == "span":
            self.math_depth += 1
        if tag in ("h1", "h2", "h3", "h4"):
            self.n_heading += 1
            hid = f"ch{self.chid}-s{line}" if line else f"ch{self.chid}-h{self.n_heading}"
            attrs = [(k, v) for k, v in attrs if k != "id"]
            extra["id"] = hid
            self.heading = {"id": hid, "level": int(tag[1]) - 1, "line": line, "title": []}
        self._emit(f"<{tag}{self._attrs(tag, attrs, extra)}>")
        self.closers.append(closing)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID or not self.closers:
            return
        closing = self.closers.pop()
        if self._suppress and closing == "":
            self._suppress = False
            return
        if self.math_start is not None and tag == "span":
            self.math_depth -= 1
            if self.math_depth == 0:
                inner = "".join(self.out[self.math_start + 1 :])
                tex = _tex_of(inner)
                start = self.out[self.math_start]
                self.out[self.math_start] = start[:-1] + f' data-tex="{html.escape(tex, quote=True)}">'
                # The visible text (what KaTeX will replace) loses labels too.
                open_c, close_c = ("\\[", "\\]") if inner.lstrip().startswith("\\[") else ("\\(", "\\)")
                self.out[self.math_start + 1 :] = [open_c + html.escape(tex, quote=False) + close_c]
                if self.heading is not None:
                    self.heading["title"].append(f"${tex}$")
                self.math_start = None
        if tag in ("h1", "h2", "h3", "h4") and self.heading is not None:
            h = self.heading
            self.heading = None
            title = re.sub(r"\s+", " ", "".join(h["title"])).strip()
            if h["level"] >= 1:
                self.toc.append({"id": h["id"], "title": title, "level": h["level"], "line": h["line"]})
        if closing:
            self._emit(closing)

    def handle_data(self, data: str) -> None:
        if self._suppress:
            return
        if self.heading is not None and self.math_start is None:
            self.heading["title"].append(html.unescape(data))
        self._emit(data)

    def handle_entityref(self, name: str) -> None:
        if self._suppress:
            return
        if self.heading is not None and self.math_start is None:
            self.heading["title"].append(html.unescape(f"&{name};"))
        self._emit(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._suppress:
            return
        if self.heading is not None and self.math_start is None:
            self.heading["title"].append(html.unescape(f"&#{name};"))
        self._emit(f"&#{name};")


_TITLE_RE = re.compile(
    r'(<div class="(?P<env>' + "|".join(THEOREM_ENVS) + r'|appproof)">)(?P<mk>(?:\s*<div id="sl-\d+">\s*</div>)*)\s*'
    r'(?:<div id="thmtitle">\s*<p>(?P<title>.*?)</p>\s*</div>)?',
    re.S,
)


def _theorem_titles(page: str, kinds: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        env = m.group("env")
        kind = kinds["proof" if env == "appproof" else env]
        name = m.group("title")
        title = f'<span class="thm-kind">{html.escape(kind)}</span>'
        if name:
            title += f' <span class="thm-name">{name}</span>'
        return f'{m.group(1)}{m.group("mk")}<div class="thm-title">{title}</div>'

    return _TITLE_RE.sub(repl, page)


def postprocess(page: str, *, course_id: int, chapter_id: int, language: str) -> tuple[str, list[dict[str, Any]]]:
    kinds = KIND_NAMES.get((language or "en")[:2], KIND_NAMES["en"])
    # pandoc's own wording for proofs: the QED box and "Proof." are replaced by the title we add.
    page = re.sub(r"<em>Proof\.</em>\s*", "", page)
    page = page.replace(" ◻", "").replace("◻", "")
    page = _theorem_titles(page, kinds)
    # Pictures point at the API.
    page = re.sub(
        r'<img src="([^"]+)"',
        lambda m: f'<img src="/api/courses/{course_id}/files/raw?path={quote(html.unescape(m.group(1)), safe="/")}"',
        page,
    )
    p = _Post(course_id, chapter_id, kinds)
    p.feed(page)
    p.close()
    return "".join(p.out), p.toc


# --------------------------------------------------------------------------- public API

_CACHE: OrderedDict[tuple[int, str, str], dict[str, Any]] = OrderedDict()
_CACHE_MAX = 200


async def render_chapter(
    *, course_id: int, language: str, chapter: dict[str, Any], source: str, blob: str, files: set[str], preamble: str = "",
) -> dict[str, Any]:
    """{chapter, html, toc, warnings, blob, took_ms} for one chapter's source."""
    key = (course_id, blob, (language or "")[:2] + str(hash(frozenset(files))) + str(hash(preamble)))
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return {**hit, "chapter": chapter, "took_ms": 0}
    t0 = time.monotonic()
    if len(source) > MAX_SOURCE:
        raise PreviewError("il capitolo è troppo grande per l'anteprima")
    latex, warnings = prepare_latex(source, files, image_limits(preamble))
    raw = await run_pandoc(latex)
    body, toc = postprocess(raw, course_id=course_id, chapter_id=chapter["id"], language=language)
    result = {"chapter": chapter, "html": body, "toc": toc, "warnings": warnings, "blob": blob,
              "took_ms": int((time.monotonic() - t0) * 1000)}
    _CACHE[key] = {k: v for k, v in result.items() if k != "chapter"}
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return result
