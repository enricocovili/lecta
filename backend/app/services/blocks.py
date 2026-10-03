"""A chapter's LaTeX split into blocks: the units the draft is typeset in, cached and selected by.

A block is a paragraph (text up to a blank line), a heading, or a whole environment (a theorem, a list, a table, a
figure…) with what is glued to it. Splitting happens only at the top level: never inside an environment, inside braces
or inside verbatim text, so every block is valid LaTeX on its own. Comment-only stretches are left out (they print
nothing). Line numbers are 1-based and refer to the stored chapter file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

VERBATIM_ENVS = {"verbatim", "verbatim*", "Verbatim", "lstlisting", "minted", "comment", "filecontents"}
_HEADING_RE = re.compile(r"^\s*\\(part|chapter|section|subsection|subsubsection|paragraph)\*?(?![A-Za-z])")
_LABEL_ONLY_RE = re.compile(r"^\s*(\\label\{[^{}]*\}\s*)+$")
_ENV_RE = re.compile(r"\\(begin|end)\s*\{([A-Za-z*@]+)\}")


@dataclass(frozen=True)
class Block:
    start: int  # first line (1-based)
    end: int  # last line (inclusive)
    src: str
    heading: bool = False

    @property
    def key(self) -> str:
        """What the block's typesetting depends on, besides the state around it."""
        return hashlib.sha256(self.src.encode()).hexdigest()


def code_part(line: str) -> str:
    """The line without its comment (an unescaped %)."""
    i = 0
    while True:
        j = line.find("%", i)
        if j < 0:
            return line
        k = j - 1
        slashes = 0
        while k >= 0 and line[k] == "\\":
            slashes += 1
            k -= 1
        if slashes % 2 == 0:
            return line[:j]
        i = j + 1


def _brace_delta(code: str) -> int:
    d = 0
    i = 0
    while i < len(code):
        c = code[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            d += 1
        elif c == "}":
            d -= 1
        i += 1
    return d


def split(source: str) -> list[Block]:
    lines = source.split("\n")
    blocks: list[Block] = []
    cur: list[int] = []  # line indices of the block being collected
    has_code = False
    heading = False
    envs: list[str] = []
    verbatim: str | None = None
    depth = 0

    def close() -> None:
        nonlocal cur, has_code, heading
        if cur and has_code:
            # Trailing comment / blank lines belong to nothing.
            while cur and not code_part(lines[cur[-1]]).strip():
                cur.pop()
            first = cur[0]
            while first < cur[-1] and not code_part(lines[first]).strip():
                first += 1
            blocks.append(Block(first + 1, cur[-1] + 1, "\n".join(lines[first : cur[-1] + 1]), heading))
        cur, has_code, heading = [], False, False

    for i, line in enumerate(lines):
        if verbatim is not None:
            cur.append(i)
            if re.search(r"\\end\s*\{" + re.escape(verbatim) + r"\}", line):
                verbatim = None
            continue
        code = code_part(line)
        top = not envs and depth <= 0
        if top and not code.strip():
            if not line.strip():
                close()
            elif cur:
                cur.append(i)  # a comment inside a paragraph
            continue
        if top:
            if _HEADING_RE.match(code) or (heading and not _LABEL_ONLY_RE.match(code)):
                close()
            if _HEADING_RE.match(code):
                heading = True
        cur.append(i)
        has_code = True
        for m in _ENV_RE.finditer(code):
            name = m.group(2)
            if m.group(1) == "begin":
                if name in VERBATIM_ENVS:
                    rest = code[m.end():]
                    if not re.search(r"\\end\s*\{" + re.escape(name) + r"\}", rest):
                        verbatim = name
                    break
                envs.append(name)
            elif name in envs:
                while envs and envs.pop() != name:
                    pass
        depth = max(0, depth + _brace_delta(code)) if verbatim is None else depth
    close()
    return blocks
