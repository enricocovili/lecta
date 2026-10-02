"""Small text helpers: slugs, LaTeX → plain text, language detection."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from . import latexmacros

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def strip_nul(value: Any) -> Any:
    """Drop NUL characters (Postgres text and JSONB can't store them; broken PDF text layers have them),
    in strings and inside lists/dicts."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, list):
        return [strip_nul(v) for v in value]
    if isinstance(value, tuple):
        return tuple(strip_nul(v) for v in value)
    if isinstance(value, dict):
        return {strip_nul(k): strip_nul(v) for k, v in value.items()}
    return value


def slugify(value: str, max_len: int = 60) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = _SLUG_RE.sub("-", value.lower()).strip("-")
    return (value[:max_len].rstrip("-")) or "untitled"


_COMMENT_RE = re.compile(r"(?<!\\)%.*")
_ENV_DROP_RE = re.compile(r"\\begin\{(tikzpicture|figure|table|circuitikz|forest|tikzcd)\}.*?\\end\{\1\}", re.S)
_HEADING_RE = re.compile(r"\\(chapter|section|subsection|subsubsection|paragraph)\*?\s*(\[[^\]]*\])?\s*\{([^{}]*)\}")
_CMD_WITH_ARG_KEEP = re.compile(r"\\(textbf|textit|emph|underline|texttt|textsc|mathrm|mathbf|text|review)\s*\{([^{}]*)\}")
_CMD_RE = re.compile(r"\\[a-zA-Z@]+\*?(\[[^\]]*\])?")
_BRACES_RE = re.compile(r"[{}]")
_WS_RE = re.compile(r"[ \t]+")


def latex_to_text(src: str) -> str:
    """Rough LaTeX markup stripping, good enough for search and embeddings."""
    s = latexmacros.without_images(_COMMENT_RE.sub("", src))
    s = _ENV_DROP_RE.sub(" ", s)
    s = _HEADING_RE.sub(lambda m: "\n" + m.group(3) + "\n", s)
    for _ in range(3):
        s = _CMD_WITH_ARG_KEEP.sub(r"\2", s)
    s = re.sub(r"\\(begin|end)\{[^}]*\}(\[[^\]]*\])?", " ", s)
    s = re.sub(r"\\(label|ref|eqref|cite|includegraphics|lectafigure)\*?(\[[^\]]*\])?\{[^}]*\}", " ", s)
    s = _CMD_RE.sub(" ", s)
    s = _BRACES_RE.sub("", s)
    s = s.replace("~", " ").replace("$", " ")
    s = _WS_RE.sub(" ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def headings(src: str) -> list[tuple[str, str]]:
    """[(level, title)] for chapter/section/subsection headings."""
    return [(m.group(1), m.group(3).strip()) for m in _HEADING_RE.finditer(_COMMENT_RE.sub("", src))]


# Tiny stopword-based detector: enough to tell apart the languages of my courses.
_STOP = {
    # Words that are frequent AND fairly distinctive for each language (shared ones like "la", "se", "un" are left out).
    "it": "il gli di della delle dei degli del nel nella nei sul sulla che non è sono per tra fra come anche più questo questa questi quando dove perché quindi ogni essere viene vale alla allo alle cui ha hanno già solo poi uno ed ma dopo sempre molto".split(),
    "en": "the of and to in is that for it as with are be this on by an from which or not can we if when where these their each have has into there been than".split(),
    "fr": "le les des du et est une que qui dans pour pas sur par avec ce cette sont au aux ou plus comme mais être sont leur".split(),
    "de": "der die das und ist nicht ein eine zu den mit von dem des sich auf für im dass wird sind auch als wie oder werden".split(),
    "es": "el los las del y que en es por para se su al como más pero sus este esta son está también muy hay cuando donde porque".split(),
}
_WORD_RE = re.compile(r"[a-zàèéìòóùäöüßçñ']+", re.I)


def detect_language(text: str, default: str = "und") -> str:
    words = [w.lower() for w in _WORD_RE.findall(text[:20000])]
    if len(words) < 8:
        return default
    sets = {lang: set(stops) for lang, stops in _STOP.items()}
    scores = {lang: sum(1 for w in words if w in st) for lang, st in sets.items()}
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    if scores[best] < max(3, len(words) * 0.03):
        return default
    return best


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def keywords_simple(q: str) -> list[str]:
    """Search terms of a query (quotes and operators stripped)."""
    return [t for t in re.findall(r"[\w\\]+", q) if len(t) > 1 and t.lower() not in ("or", "and", "not")]
