r"""The stored courses become plain LaTeX: picture commands written out, review notes as todonotes, no Lecta hooks

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-15 12:00:00

The text a course stores used macros defined only in Lecta's preamble (DECISIONS 139). This rewrites it into the
standard LaTeX the app now writes:

* `\lectaimage`, `\lectaimagewithtext`, `\lectaimagepair` → `figure` + `minipage` + `\includegraphics` with adjustbox's
  limits, the sizes the course's definitions had (a course's own preamble may have drawn `\lectaimage` smaller);
* `\review{…}` → `\todo[inline]{…}` (todonotes, hidden in published builds);
* `main.tex` loses `\def\lectalang{…}` and the `\ifdefined\lectaincludeonly` hook (the build's root file issues
  `\includeonly` itself); pandoc's `\tightlist` goes;
* a course's own preamble (and the global template) loses the definitions of those macros and of `\lectaname`, whose
  theorem names are written in the course's language; one that was a copy of the old default preamble is dropped,
  so the course follows the new default.

Like 0017: every changed text file becomes a new blob (the old one stays on disk), the undo snapshots of the
assistant's answers and the chapter index follow the new hashes, and draft notes are rewritten too. Self-contained on
purpose: a migration must keep working when the app code around it changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

from app.config import config

revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None

TEXT_EXT = (".tex", ".bib", ".sty", ".cls", ".bst", ".txt", ".md", ".csv", ".dat", ".tikz")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_SIZE = (0.5, 0.22)

THEOREM_NAMES = {
    "it": ("Teorema", "Lemma", "Proposizione", "Corollario", "Definizione", "Esempio", "Osservazione"),
    "en": ("Theorem", "Lemma", "Proposition", "Corollary", "Definition", "Example", "Remark"),
    "fr": ("Théorème", "Lemme", "Proposition", "Corollaire", "Définition", "Exemple", "Remarque"),
    "de": ("Satz", "Lemma", "Proposition", "Korollar", "Definition", "Beispiel", "Bemerkung"),
    "es": ("Teorema", "Lema", "Proposición", "Corolario", "Definición", "Ejemplo", "Observación"),
    "pt": ("Teorema", "Lema", "Proposição", "Corolário", "Definição", "Exemplo", "Observação"),
}
THEOREMS = ("theorem", "lemma", "proposition", "corollary", "definition", "example", "remark")

# The default preamble as it was before this revision: an override equal to it (once the picture macros, which a
# course could resize, and the comments are left aside) is a copy of the default and is dropped.
OLD_DEFAULT = r"""% Lecta default preamble.
% Shared by the notes and by the standalone figure builds: keep layout packages
% (geometry, hyperref, ...) in main.tex, not here.
\usepackage{iftex}
\ifPDFTeX
  \usepackage[T1]{fontenc}
  \usepackage[utf8]{inputenc}
  \usepackage{lmodern}
\else
  \usepackage{fontspec}
\fi
\usepackage{amsmath,amssymb,amsthm,mathtools}
\usepackage{graphicx}
\usepackage[export]{adjustbox}
\usepackage{xcolor}
\usepackage{booktabs,array,tabularx,multirow,longtable,calc}
\usepackage{url}
\usepackage{float}
\usepackage{enumitem}
\usepackage[most]{tcolorbox}
\usepackage{tikz}
\usetikzlibrary{arrows.meta,positioning,calc,shapes,shapes.geometric,fit,backgrounds,
  decorations.pathreplacing,automata,matrix,patterns,intersections,quotes,angles,babel}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage{circuitikz}
\usepackage{tikz-cd}
\usepackage{forest}
\usepackage{chemfig}

% pandoc compatibility (Markdown notes are converted with pandoc)
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}

% ---- localised names ------------------------------------------------------
\providecommand{\lectalang}{english}
\makeatletter
\def\lecta@name@italian@definition{Definizione}
\def\lecta@name@italian@theorem{Teorema}
\def\lecta@name@italian@lemma{Lemma}
\def\lecta@name@italian@proposition{Proposizione}
\def\lecta@name@italian@corollary{Corollario}
\def\lecta@name@italian@example{Esempio}
\def\lecta@name@italian@remark{Osservazione}
\def\lecta@name@english@definition{Definition}
\def\lecta@name@english@theorem{Theorem}
\def\lecta@name@english@lemma{Lemma}
\def\lecta@name@english@proposition{Proposition}
\def\lecta@name@english@corollary{Corollary}
\def\lecta@name@english@example{Example}
\def\lecta@name@english@remark{Remark}
\newcommand{\lectaname}[1]{%
  \@ifundefined{lecta@name@\lectalang @#1}%
    {\csname lecta@name@english@#1\endcsname}%
    {\csname lecta@name@\lectalang @#1\endcsname}}

% ---- theorem-like environments (amsthm + tcolorbox) -----------------------
\@ifundefined{c@chapter}%
  {\newtheorem{theorem}{\lectaname{theorem}}}%
  {\newtheorem{theorem}{\lectaname{theorem}}[chapter]}
\makeatother
\newtheorem{lemma}[theorem]{\lectaname{lemma}}
\newtheorem{proposition}[theorem]{\lectaname{proposition}}
\newtheorem{corollary}[theorem]{\lectaname{corollary}}
\theoremstyle{definition}
\newtheorem{definition}[theorem]{\lectaname{definition}}
\newtheorem{example}[theorem]{\lectaname{example}}
\theoremstyle{remark}
\newtheorem{remark}[theorem]{\lectaname{remark}}
\tcolorboxenvironment{definition}{enhanced jigsaw,breakable,colback=blue!3,colframe=blue!45!black,boxrule=0.5pt,left=4pt,right=4pt}
\tcolorboxenvironment{theorem}{enhanced jigsaw,breakable,colback=red!3,colframe=red!50!black,boxrule=0.5pt,left=4pt,right=4pt}
\tcolorboxenvironment{lemma}{enhanced jigsaw,breakable,colback=red!2,colframe=red!35!black,boxrule=0.4pt,left=4pt,right=4pt}
\tcolorboxenvironment{proposition}{enhanced jigsaw,breakable,colback=red!2,colframe=red!35!black,boxrule=0.4pt,left=4pt,right=4pt}
\tcolorboxenvironment{corollary}{enhanced jigsaw,breakable,colback=red!2,colframe=red!35!black,boxrule=0.4pt,left=4pt,right=4pt}
\tcolorboxenvironment{example}{enhanced jigsaw,breakable,colback=green!3,colframe=green!40!black,boxrule=0.4pt,left=4pt,right=4pt}

% ---- review marker ----------------------------------------------------------
% Draft builds show a highlighted note; published builds (\lectapublish) drop it.
\ifdefined\lectapublish
  \newcommand{\review}[1]{\ignorespaces}
\else
  \newcommand{\review}[1]{%
    \begin{tcolorbox}[enhanced jigsaw,breakable,colback=yellow!20,colframe=orange!85!black,
      boxrule=0.6pt,size=small,title={\small\bfseries Review},fonttitle=\small]#1\end{tcolorbox}}
\fi

% ---- figures ----------------------------------------------------------------
% \lectafigure[caption]{name} includes figures/name.tex, precompiled to PDF.
\newcommand{\lectafigure}[2][]{%
  \begin{figure}[H]\centering
  \IfFileExists{figures-cache/#2.pdf}%
    {\includegraphics[max width=\linewidth,max height=0.7\textheight]{figures-cache/#2.pdf}}%
    {\PackageWarning{lecta}{figure #2 is not available (figures/#2.tex did not compile)}%
     \fbox{\parbox{0.8\linewidth}{\centering\ttfamily figure #2 not available}}}%
  \if\relax\detokenize{#1}\relax\else\caption{#1}\fi
  \end{figure}}
% \lectaimage[caption]{images/name.png} places an image copied from the sources, small (at most half the line wide).
\newcommand{\lectaimage}[2][]{%
  \begin{figure}[H]\centering
  \IfFileExists{#2}%
    {\includegraphics[max width=0.5\linewidth,max height=0.22\textheight]{#2}}%
    {\PackageWarning{lecta}{image #2 not found}%
     \fbox{\parbox{0.8\linewidth}{\centering\ttfamily image not available}}}%
  \if\relax\detokenize{#1}\relax\else\caption{#1}\fi
  \end{figure}}
% \lectaimagewithtext[caption]{images/name.png}{text}: a small image with the text that explains it beside it.
% An empty image name leaves the text alone.
\newcommand{\lectaimagewithtext}[3][]{%
  \begin{figure}[H]\noindent
  \if\relax\detokenize{#2}\relax
    \begin{minipage}[c]{\linewidth}#3\end{minipage}%
  \else
    \begin{minipage}[c]{0.39\linewidth}\centering
      \IfFileExists{#2}%
        {\includegraphics[max width=\linewidth,max height=0.27\textheight]{#2}}%
        {\PackageWarning{lecta}{image #2 not found}\fbox{\parbox{0.9\linewidth}{\centering\ttfamily image not available}}}%
    \end{minipage}\hfill
    \begin{minipage}[c]{0.57\linewidth}#3\end{minipage}%
  \fi
  \if\relax\detokenize{#1}\relax\else\caption{#1}\fi
  \end{figure}}
% \lectaimagepair{caption 1}{image 1}{caption 2}{image 2}: two small images on one row, each with its caption.
\newcommand{\lectaimagepair}[4]{%
  \begin{figure}[H]\centering
  \begin{minipage}[t]{0.48\linewidth}\centering
    \IfFileExists{#2}{\includegraphics[max width=\linewidth,max height=0.32\textheight]{#2}}%
      {\fbox{\parbox{0.9\linewidth}{\centering\ttfamily image not available}}}\par
    {\small #1}
  \end{minipage}\hfill
  \begin{minipage}[t]{0.48\linewidth}\centering
    \IfFileExists{#4}{\includegraphics[max width=\linewidth,max height=0.32\textheight]{#4}}%
      {\fbox{\parbox{0.9\linewidth}{\centering\ttfamily image not available}}}\par
    {\small #3}
  \end{minipage}
  \end{figure}}
"""


# ------------------------------------------------------------------------------ parsing


def _match_brace(s: str, i: int, open_c: str = "{", close_c: str = "}") -> int:
    if i >= len(s) or s[i] != open_c:
        return -1
    depth, j = 0, i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def _macro_spans(src: str, name: str, n_required: int) -> list[tuple[int, int, str | None, list[str]]]:
    out = []
    for m in re.finditer(r"\\" + re.escape(name) + r"(?![A-Za-z@])", src):
        j = m.end()
        while j < len(src) and src[j] in " \t":
            j += 1
        opt = None
        if j < len(src) and src[j] == "[":
            k = _match_brace(src, j, "[", "]")
            if k < 0:
                continue
            opt, j = src[j + 1 : k - 1], k
            while j < len(src) and src[j] in " \t":
                j += 1
        args: list[str] = []
        for _ in range(n_required):
            while j < len(src) and src[j] in " \t\n":
                j += 1
            k = _match_brace(src, j)
            if k < 0:
                break
            args.append(src[j + 1 : k - 1])
            j = k
        if len(args) == n_required:
            out.append((m.start(), j, opt, args))
    return out


# ------------------------------------------------------------------------------ pictures

COMMANDS = {"lectaimage": 1, "lectaimagewithtext": 2, "lectaimagepair": 4}


def _graphic(path: str, width: str, height: float) -> str:
    return f"\\includegraphics[max width={width},max height={height:g}\\textheight]{{{path.strip()}}}"


def _caption(caption: str | None) -> str:
    return f"\\caption{{{caption.strip()}}}\n" if caption and caption.strip() else ""


def _expand(name: str, opt: str | None, args: list[str], size: tuple[float, float]) -> str:
    if name == "lectaimage":
        if not args[0].strip():
            return ""
        return ("\\begin{figure}[H]\n\\centering\n" + _graphic(args[0], f"{size[0]:g}\\linewidth", size[1]) + "\n"
                + _caption(opt) + "\\end{figure}")
    if name == "lectaimagewithtext":
        path, text = args[0], args[1].strip()
        if not path.strip():
            return text
        return ("\\begin{figure}[H]\n\\noindent\n"
                "\\begin{minipage}[c]{0.39\\linewidth}\n\\centering\n" + _graphic(path, "\\linewidth", 0.27) + "\n"
                "\\end{minipage}\\hfill\n"
                "\\begin{minipage}[c]{0.57\\linewidth}\n" + text + "\n\\end{minipage}\n"
                + _caption(opt) + "\\end{figure}")
    pairs = [(args[0], args[1]), (args[2], args[3])]
    kept = [(c.strip(), p) for c, p in pairs if p.strip()]
    if not kept:
        return "\n\n".join(c.strip() for c, _p in pairs if c.strip())

    def cell(caption: str, path: str) -> str:
        return _graphic(path, "\\linewidth", 0.32) + "\\par\n" + (f"{{\\small {caption}}}\n" if caption else "")

    if len(kept) == 1:
        return ("\\begin{figure}[H]\n\\centering\n\\begin{minipage}[t]{0.48\\linewidth}\n\\centering\n" + cell(*kept[0])
                + "\\end{minipage}\n\\end{figure}")
    return ("\\begin{figure}[H]\n\\centering\n"
            + "\\hfill\n".join("\\begin{minipage}[t]{0.48\\linewidth}\n\\centering\n" + cell(c, p) + "\\end{minipage}" for c, p in kept)
            + "\n\\end{figure}")


def expand_images(src: str, size: tuple[float, float]) -> str:
    spans = sorted((s, e, name, o, a) for name, n in COMMANDS.items() for s, e, o, a in _macro_spans(src, name, n))
    out, pos = [], 0
    for start, end, name, opt, args in spans:
        if start < pos:
            continue
        before = src[pos:start]
        new = _expand(name, opt, args, size)
        if new and before and not before.endswith("\n"):
            before = before.rstrip(" \t") + "\n"
        out.append(before + new)
        pos = end
        if new:
            while pos < len(src) and src[pos] in " \t":
                pos += 1
            if pos < len(src) and src[pos] != "\n":
                out.append("\n")
    out.append(src[pos:])
    return "".join(out)


# ------------------------------------------------------------------------------ text files

_REVIEW = re.compile(r"\\review(?![A-Za-z@])\s*\{")
_HOOKS = [
    re.compile(r"(?m)^[ \t]*\\def\\lectalang\{[^{}\n]*\}[ \t]*\n"),
    re.compile(r"(?m)^% The editor compiles only the chapter being edited\.[ \t]*\n"),
    re.compile(r"(?m)^[ \t]*\\ifdefined\\lectaincludeonly\\includeonly\{\\lectaincludeonly\}\\fi[ \t]*\n"),
]
_TIGHTLIST = re.compile(r"[ \t]*\\tightlist(?![A-Za-z@])[ \t]*\n?")


def rewrite_text(s: str, size: tuple[float, float]) -> str:
    s = expand_images(s, size)
    s = _REVIEW.sub(r"\\todo[inline]{", s)
    for pat in _HOOKS:
        s = pat.sub("", s)
    return _TIGHTLIST.sub("", s)


# ------------------------------------------------------------------------------ preambles

_LEGACY_DEFS = ("lectafigure", "lectaimage", "lectaimagewithtext", "lectaimagepair", "lectaname")
_LEGACY_COMMENT = re.compile(r"\\lecta|\\review|localised names|review marker|---- figures|pandoc compatibility|"
                             r"Draft builds show|An empty image name|A compact image placed|Two source images")


def image_size(preamble: str | None) -> tuple[float, float]:
    """The limits a preamble's own \\lectaimage drew its picture with."""
    m = re.search(r"\\newcommand\s*\{\\lectaimage\}(?:\[[^\]]*\])*\s*(?=\{)", preamble or "")
    if m:
        end = _match_brace(preamble, m.end())
        lim = re.search(r"max width=([\d.]+)\\linewidth,\s*max height=([\d.]+)\\textheight", preamble[m.end() : end])
        if lim:
            return float(lim.group(1)), float(lim.group(2))
    return DEFAULT_SIZE


def _drop_newcommand(s: str, name: str) -> str:
    while True:
        m = re.search(r"\\newcommand\s*\{\\" + name + r"\}", s)
        if not m:
            return s
        j = m.end()
        while j < len(s) and s[j] in " \t[":
            if s[j] == "[":
                j = _match_brace(s, j, "[", "]")
                if j < 0:
                    return s
            else:
                j += 1
        k = _match_brace(s, j)
        if k < 0:
            return s
        end = k + 1 if k < len(s) and s[k] == "\n" else k
        s = s[: m.start()] + s[end:]


def clean_preamble(s: str, language: str | None) -> str:
    names = dict(zip(THEOREMS, THEOREM_NAMES.get((language or "").lower(), THEOREM_NAMES["en"]), strict=True))
    s = re.sub(r"\\ifdefined\\lectapublish\b.*?\\fi[ \t]*\n?", "", s, flags=re.S)
    for name in _LEGACY_DEFS:
        s = _drop_newcommand(s, name)
    s = re.sub(r"(?m)^[ \t]*\\providecommand\{\\(?:lectalang|tightlist)\}.*\n?", "", s)
    s = re.sub(r"(?m)^[ \t]*\\def\\lecta@name@[A-Za-z]+@[A-Za-z]+\{[^{}\n]*\}[ \t]*\n?", "", s)
    s = re.sub(r"\\lectaname\{([A-Za-z]+)\}", lambda m: names.get(m.group(1), m.group(1)), s)
    s = "\n".join(ln for ln in s.split("\n") if not (ln.lstrip().startswith("%") and _LEGACY_COMMENT.search(ln)))
    return re.sub(r"\n{3,}", "\n\n", s).strip() + "\n"


def _norm(s: str) -> list[str]:
    return [ln.strip() for ln in re.sub(r"(?<!\\)%.*", "", s).split("\n") if ln.strip()]


def new_override(override: str | None, language: str | None) -> str | None:
    if not override:
        return override
    cleaned = clean_preamble(override, language)
    if _norm(cleaned) == _norm(clean_preamble(OLD_DEFAULT, language)):
        return None
    return cleaned


# ------------------------------------------------------------------------------ blobs


def _blob_path(h: str) -> Path:
    return Path(config.data_dir) / "blobs" / h[:2] / h[2:4] / h


def _rewrite_blob(h: str, size: tuple[float, float]) -> tuple[str, str, int] | None:
    """(new hash, new text, size in bytes), or None when the blob is missing, binary or has nothing to rewrite."""
    if not HEX64.match(h or ""):
        return None
    try:
        old = _blob_path(h).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    new = rewrite_text(old, size)
    if new == old:
        return None
    data = new.encode("utf-8")
    nh = hashlib.sha256(data).hexdigest()
    target = _blob_path(nh)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{nh}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return nh, new, len(data)


def _is_text_path(path: str) -> bool:
    return path.lower().endswith(TEXT_EXT)


def _walk_files(change: dict[str, Any], fn, language: str | None) -> dict[str, Any]:  # noqa: ANN001
    """The undo snapshot with every text file's blob passed through fn(old hash) -> new hash | None, and its preamble
    rewritten like the course's (undo compares the course's preamble with the one the answer left)."""
    out = dict(change)
    for side in ("pre", "post"):
        snap = change.get(side)
        if not isinstance(snap, dict) or not isinstance(snap.get("files"), dict):
            continue
        files = {}
        for path, value in snap["files"].items():
            h = value.get("blob") if isinstance(value, dict) else value
            nh = fn(h) if _is_text_path(path) and isinstance(h, str) else None
            if nh:
                value = {**value, "blob": nh} if isinstance(value, dict) else nh
            files[path] = value
        out[side] = {**snap, "files": files}
        if isinstance(snap.get("preamble"), str):
            out[side]["preamble"] = new_override(snap["preamble"], language)
    return out


def upgrade() -> None:
    bind = op.get_bind()
    courses = bind.execute(sa.text("SELECT id, language, preamble_override FROM courses")).all()
    sizes = {cid: image_size(override) for cid, _lang, override in courses}
    done: dict[tuple[str, tuple[float, float]], str | None] = {}

    def remap(h: str, size: tuple[float, float]) -> str | None:
        if (h, size) not in done:
            r = _rewrite_blob(h, size)
            done[(h, size)] = r[0] if r else None
        return done[(h, size)]

    # The files of the courses.
    rows = bind.execute(sa.text("SELECT id, course_id, path, blob FROM project_files WHERE is_text")).all()
    for fid, cid, path, blob in rows:
        if not _is_text_path(path):
            continue
        size = sizes.get(cid, DEFAULT_SIZE)
        r = _rewrite_blob(blob, size)
        if not r:
            continue
        nh, text, n = r
        done[(blob, size)] = nh
        bind.execute(sa.text("UPDATE project_files SET blob = :b, size = :n, text_content = :t WHERE id = :id"),
                     {"b": nh, "n": n, "t": text, "id": fid})
        bind.execute(sa.text("UPDATE index_state SET blob = :new WHERE blob = :old AND chapter_id IN "
                             "(SELECT id FROM chapters WHERE course_id = :cid)"), {"old": blob, "new": nh, "cid": cid})

    # The undo snapshots of the assistant's answers on the course text follow the same hashes.
    msgs = bind.execute(sa.text(
        "SELECT m.id, s.course_id, c.language, m.change::text FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id "
        "JOIN courses c ON c.id = s.course_id WHERE m.change IS NOT NULL AND s.lab_id IS NULL")).all()
    for mid, cid, lang, change in msgs:
        old = json.loads(change)
        size = sizes.get(cid, DEFAULT_SIZE)
        new = _walk_files(old, lambda h, size=size: remap(h, size), lang)
        if new != old:
            bind.execute(sa.text("UPDATE chat_messages SET change = CAST(:c AS jsonb) WHERE id = :id"),
                         {"c": json.dumps(new, ensure_ascii=False), "id": mid})

    # Draft notes quote a block's LaTeX (and may propose new LaTeX).
    for nid, cid, src, proposed in bind.execute(sa.text("SELECT id, course_id, src, proposed FROM draft_notes")).all():
        size = sizes.get(cid, DEFAULT_SIZE)
        ns, np_ = rewrite_text(src or "", size) if src else src, rewrite_text(proposed, size) if proposed else proposed
        if (ns, np_) != (src, proposed):
            bind.execute(sa.text("UPDATE draft_notes SET src = :s, proposed = :p WHERE id = :id"), {"s": ns, "p": np_, "id": nid})

    # Preambles: the course's own, and the global template.
    for cid, lang, override in courses:
        new = new_override(override, lang)
        if new != override:
            bind.execute(sa.text("UPDATE courses SET preamble_override = :p WHERE id = :id"), {"p": new, "id": cid})
    row = bind.execute(sa.text("SELECT value::text FROM settings WHERE key = 'template'")).first()
    if row:
        value = json.loads(row[0])
        if isinstance(value, dict) and isinstance(value.get("preamble"), str):
            new = new_override(value["preamble"], "it")
            if new != value["preamble"]:
                value["preamble"] = new
                bind.execute(sa.text("UPDATE settings SET value = CAST(:v AS jsonb) WHERE key = 'template'"),
                             {"v": json.dumps(value, ensure_ascii=False)})


def downgrade() -> None:
    # The macros are not restored: the code of the previous revision no longer defines them for new text either.
    pass
