"""LaTeX project templates.

Layout of a course project:

    main.tex              document class, layout, \\input{preamble}, managed \\include block
    preamble.tex          generated at build time from the course override or global template
    chapters/NN-slug.tex  one file per chapter, pulled in with \\include
    figures/*.tex         one diagram per file (just the picture code), pulled in with \\input
    images/               raster images

Layout packages (geometry, hyperref) live in main.tex, not in the preamble.
"""

from __future__ import annotations

import re

BABEL = {
    "it": "italian",
    "en": "english",
    "fr": "french",
    "de": "ngerman",
    "es": "spanish",
    "pt": "portuguese",
}

TS_CONFIG = {
    "it": "italian",
    "en": "english",
    "fr": "french",
    "de": "german",
    "es": "spanish",
    "pt": "portuguese",
}


def ts_config(lang: str | None) -> str:
    return TS_CONFIG.get((lang or "").lower(), "simple")


_DEFAULT_PREAMBLE = r"""% Lecta default preamble.
% Layout packages (geometry, hyperref, ...) live in main.tex, not here.
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
\setlength{\marginparwidth}{2cm}  % what todonotes wants, or it warns on every build
\usepackage{todonotes}  % review notes: \todo[inline]{...}, hidden when published (option disable)
\usepackage{soul}       % \ul{...} underlines, \hl{...} highlights: unlike \underline, they break across lines
\usepackage{tikz}
\usetikzlibrary{arrows.meta,positioning,calc,shapes,shapes.geometric,fit,backgrounds,
  decorations.pathreplacing,automata,matrix,patterns,intersections,quotes,angles,babel}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage{circuitikz}
\usepackage{tikz-cd}
\usepackage{forest}
\usepackage{chemfig}

% A line stretches its spaces a little more before running past the margin.
\setlength{\emergencystretch}{3em}

% ---- theorem-like environments (amsthm + tcolorbox) -----------------------
% Numbered within chapters when the class has them; the names are in the course's language.
\makeatletter
\@ifundefined{c@chapter}{\newtheorem{theorem}{@@theorem@@}}{\newtheorem{theorem}{@@theorem@@}[chapter]}
\makeatother
\newtheorem{lemma}[theorem]{@@lemma@@}
\newtheorem{proposition}[theorem]{@@proposition@@}
\newtheorem{corollary}[theorem]{@@corollary@@}
\theoremstyle{definition}
\newtheorem{definition}[theorem]{@@definition@@}
\newtheorem{example}[theorem]{@@example@@}
\theoremstyle{remark}
\newtheorem{remark}[theorem]{@@remark@@}
\tcolorboxenvironment{definition}{enhanced jigsaw,breakable,colback=blue!3,colframe=blue!45!black,boxrule=0.5pt,left=4pt,right=4pt}
\tcolorboxenvironment{theorem}{enhanced jigsaw,breakable,colback=red!3,colframe=red!50!black,boxrule=0.5pt,left=4pt,right=4pt}
\tcolorboxenvironment{lemma}{enhanced jigsaw,breakable,colback=red!2,colframe=red!35!black,boxrule=0.4pt,left=4pt,right=4pt}
\tcolorboxenvironment{proposition}{enhanced jigsaw,breakable,colback=red!2,colframe=red!35!black,boxrule=0.4pt,left=4pt,right=4pt}
\tcolorboxenvironment{corollary}{enhanced jigsaw,breakable,colback=red!2,colframe=red!35!black,boxrule=0.4pt,left=4pt,right=4pt}
\tcolorboxenvironment{example}{enhanced jigsaw,breakable,colback=green!3,colframe=green!40!black,boxrule=0.4pt,left=4pt,right=4pt}
"""

# The names of the theorem-like environments, per course language (the others get the English ones).
THEOREM_NAMES = {
    "it": ("Teorema", "Lemma", "Proposizione", "Corollario", "Definizione", "Esempio", "Osservazione"),
    "en": ("Theorem", "Lemma", "Proposition", "Corollary", "Definition", "Example", "Remark"),
    "fr": ("Théorème", "Lemme", "Proposition", "Corollaire", "Définition", "Exemple", "Remarque"),
    "de": ("Satz", "Lemma", "Proposition", "Korollar", "Definition", "Beispiel", "Bemerkung"),
    "es": ("Teorema", "Lema", "Proposición", "Corolario", "Definición", "Ejemplo", "Observación"),
    "pt": ("Teorema", "Lema", "Proposição", "Corolário", "Definição", "Exemplo", "Observação"),
}
_THEOREMS = ("theorem", "lemma", "proposition", "corollary", "definition", "example", "remark")


def default_preamble(language: str | None) -> str:
    """The default preamble of a course in `language`: the theorem names are written in it, so the preamble needs no
    macro to pick them (and stays correct when copied into another project)."""
    names = THEOREM_NAMES.get((language or "").lower(), THEOREM_NAMES["en"])
    out = _DEFAULT_PREAMBLE
    for key, name in zip(_THEOREMS, names, strict=True):
        out = out.replace(f"@@{key}@@", name)
    return out


# Appended to every preamble at build time: a course with its own preamble gets the packages the text the app writes
# relies on (figures with adjustbox's limits, review notes, underlines and highlights that break across lines).
COMPAT_TAIL = r"""
% ---- added by Lecta at build time: packages the text relies on --------------
\makeatletter
\@ifpackageloaded{graphicx}{}{\RequirePackage{graphicx}}
\@ifpackageloaded{adjustbox}{}{\RequirePackage[export]{adjustbox}}
\@ifpackageloaded{float}{}{\RequirePackage{float}}
\@ifpackageloaded{todonotes}{}{\setlength{\marginparwidth}{2cm}\RequirePackage{todonotes}}
\@ifpackageloaded{soul}{}{\RequirePackage{soul}}
\makeatother
"""


def with_compat(preamble: str) -> str:
    return preamble.rstrip() + "\n" + COMPAT_TAIL


MAIN_BEGIN = "% lecta:chapters:begin (managed automatically, do not edit between these lines)"
MAIN_END = "% lecta:chapters:end"


def _tex_escape(s: str) -> str:
    repl = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(repl.get(c, c) for c in s)


tex_escape = _tex_escape


def chapter_block(chapter_paths: list[str]) -> str:
    lines = [MAIN_BEGIN]
    for p in chapter_paths:
        lines.append(r"\include{" + p.removesuffix(".tex") + "}")
    lines.append(MAIN_END)
    return "\n".join(lines)


def main_tex(name: str, year: str | None, language: str, chapter_paths: list[str]) -> str:
    babel = BABEL.get(language, "english")
    return rf"""\documentclass[11pt,a4paper,oneside]{{report}}
\input{{preamble}}
\usepackage[{babel}]{{babel}}
\usepackage[a4paper,margin=2.5cm]{{geometry}}
\usepackage[hidelinks]{{hyperref}}

\title{{{_tex_escape(name)}}}
\author{{}}
\date{{{_tex_escape(year or "")}}}

\begin{{document}}
\maketitle
\tableofcontents

{chapter_block(chapter_paths)}

\end{{document}}
"""


def chapter_tex(title: str, slug: str) -> str:
    return "\\chapter{" + _tex_escape(title) + "}\\label{ch:" + slug + "}\n\n"


_BLOCK_RE = re.compile(re.escape(MAIN_BEGIN) + r".*?" + re.escape(MAIN_END), re.S)


def replace_chapter_block(main: str, chapter_paths: list[str]) -> str:
    block = chapter_block(chapter_paths)
    if _BLOCK_RE.search(main):
        return _BLOCK_RE.sub(lambda _m: block, main, count=1)
    # Block removed by hand: re-insert it before \end{document}.
    idx = main.rfind(r"\end{document}")
    if idx < 0:
        return main.rstrip() + "\n" + block + "\n"
    return main[:idx] + block + "\n\n" + main[idx:]
