"""LaTeX project templates.

Layout of a course project:

    main.tex              document class, layout, \\input{preamble}, managed \\include block
    preamble.tex          generated at build time from the course override or global template
    chapters/NN-slug.tex  one file per chapter, pulled in with \\include
    figures/*.tex         one diagram per file (just the picture code); precompiled standalone
    images/               raster images

The preamble is shared by the full document and the standalone figure builds, so
it must not load layout packages (geometry, hyperref): those live in main.tex.
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


DEFAULT_PREAMBLE = r"""% Lecta default preamble.
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

# Appended to every preamble at build time: courses with their own preamble (written before
# \lectaimage existed) still compile chapters that use it. Needs only graphicx.
COMPAT_TAIL = r"""
% ---- added by Lecta at build time ------------------------------------------
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\makeatletter
\@ifundefined{lectaimage}{%
  \@ifpackageloaded{graphicx}{}{\RequirePackage{graphicx}}%
  \newcommand{\lectaimage}[2][]{\par\begin{center}\IfFileExists{#2}{\includegraphics[width=.85\linewidth,height=.6\textheight,keepaspectratio]{#2}}{\fbox{image not available}}%
    \if\relax\detokenize{#1}\relax\else\par{\small #1}\fi\end{center}\par}}{}
\@ifundefined{lectaimagewithtext}{%
  \@ifpackageloaded{graphicx}{}{\RequirePackage{graphicx}}%
  \newcommand{\lectaimagewithtext}[3][]{\par\medskip\noindent
    \if\relax\detokenize{#2}\relax\begin{minipage}[c]{\linewidth}#3\end{minipage}\else
    \begin{minipage}[c]{0.39\linewidth}\centering\IfFileExists{#2}{\includegraphics[width=\linewidth,height=0.27\textheight,keepaspectratio]{#2}}{\fbox{image not available}}\end{minipage}\hfill
    \begin{minipage}[c]{0.57\linewidth}#3\end{minipage}\fi
    \if\relax\detokenize{#1}\relax\else\par{\small #1}\fi\par\medskip}}{}
\@ifundefined{lectaimagepair}{%
  \@ifpackageloaded{graphicx}{}{\RequirePackage{graphicx}}%
  \newcommand{\lectaimagepair}[4]{\par\medskip\noindent
    \begin{minipage}[t]{0.48\linewidth}\centering\IfFileExists{#2}{\includegraphics[width=\linewidth,height=0.32\textheight,keepaspectratio]{#2}}{\fbox{image not available}}\par{\small #1}\end{minipage}\hfill
    \begin{minipage}[t]{0.48\linewidth}\centering\IfFileExists{#4}{\includegraphics[width=\linewidth,height=0.32\textheight,keepaspectratio]{#4}}{\fbox{image not available}}\par{\small #3}\end{minipage}\par\medskip}}{}
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
    lecta_lang = "italian" if language == "it" else "english"
    return rf"""\documentclass[11pt,a4paper,oneside]{{report}}
\def\lectalang{{{lecta_lang}}}
\input{{preamble}}
\usepackage[{babel}]{{babel}}
\usepackage[a4paper,margin=2.5cm]{{geometry}}
\usepackage[hidelinks]{{hyperref}}

\title{{{_tex_escape(name)}}}
\author{{}}
\date{{{_tex_escape(year or "")}}}

% The editor compiles only the chapter being edited.
\ifdefined\lectaincludeonly\includeonly{{\lectaincludeonly}}\fi

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


FIGURE_WRAPPER = r"""\documentclass[border=3pt,varwidth]{standalone}
\def\lectastandalone{1}
\input{preamble}
\begin{document}
\input{figures/%s}
\end{document}
"""
