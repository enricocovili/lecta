"""Tables that show up as tables in the PDF.

The text the import and the assistant write uses `tabular` / `tabularx`, and often without any rule: typeset, that is a few
loose lines of text, while the HTML draft shows it as a table. When the PDF is built every such table gets booktabs rules
(`\\toprule` under the column spec, `\\bottomrule` before the end) and is started on a new line (`\\noindent`, so a table after
a paragraph is not indented like one). The stored text is not changed, and nothing moves: the rules are added on the lines the
table already uses, so the line numbers of the errors stay right.

A table that has rules of its own (`\\hline`, booktabs commands, `|` in the column spec) is left as its author made it.
"""

from __future__ import annotations

import re

from .latexmacros import match_brace

_BEGIN = re.compile(r"\\begin\{(tabularx|tabular)\}")
_RULES = re.compile(r"\\(toprule|midrule|bottomrule|hline|cline|cmidrule|specialrule)\b")
_SPACE = " \t\n"


def _end_of(src: str, name: str, start: int) -> int:
    """Index of the `\\end{name}` that closes the environment whose body starts at `start` (or -1), nesting respected."""
    depth = 1
    pat = re.compile(r"\\(begin|end)\{" + name + r"\}")
    for m in pat.finditer(src, start):
        depth += 1 if m.group(1) == "begin" else -1
        if depth == 0:
            return m.start()
    return -1


def add_rules(src: str) -> str:
    """The text with rules added to the tables that have none."""
    out: list[str] = []
    pos = 0
    for m in _BEGIN.finditer(src):
        if m.start() < pos:
            continue  # inside a table already handled
        name = m.group(1)
        j = m.end()
        if j < len(src) and src[j] == "[":  # vertical position
            k = match_brace(src, j, "[", "]")
            if k < 0:
                continue
            j = k
        spec = ""
        ok = True
        for _ in range(2 if name == "tabularx" else 1):
            while j < len(src) and src[j] in _SPACE:
                j += 1
            k = match_brace(src, j)
            if k < 0:
                ok = False
                break
            spec = src[j + 1 : k - 1]
            j = k
        if not ok or "|" in spec:
            continue
        end = _end_of(src, name, j)
        if end < 0:
            continue
        body = src[j:end]
        if _RULES.search(body) or not body.strip():
            continue
        # Where the table starts a line, it starts a new line of the page, not a paragraph's indentation.
        line_start = src.rfind("\n", 0, m.start()) + 1
        lead = "\\noindent " if not src[line_start : m.start()].strip() and "noindent" not in src[line_start : m.start()] else ""
        closing = "" if re.search(r"\\\\\s*(\\\\\[[^\]]*\]\s*)?$", body.rstrip()) else "\\\\ "
        if body.rstrip().endswith("\\\\"):
            closing = ""
        out.append(src[pos : m.start()] + lead + src[m.start() : j] + "\\toprule " + body.rstrip(" \t") + closing + "\\bottomrule ")
        pos = end
    out.append(src[pos:])
    return "".join(out)
