"""Parse TeX/LaTeX logs into diagnostics: [{level, file, line, message, context}].

Logs are produced with -file-line-error and max_print_line=10000 (no wrapping),
so errors look like `./chapters/01-x.tex:12: Undefined control sequence.`.
Warnings don't carry a file name, so a parenthesis-based file stack tracks which
file TeX was reading.
"""

from __future__ import annotations

import re

_FILE_ERR_RE = re.compile(r"^(\.?/?[^\s:()]+\.(?:tex|sty|cls|ltx|def|cfg|clo|bbl|aux)):(\d+): (.*)$")
_BANG_RE = re.compile(r"^! (.*)$")
_WARN_RE = re.compile(r"^(LaTeX|Package ([\w@.-]+)|Class ([\w@.-]+)|pdfTeX|LuaTeX|XeTeX)( Font)? Warning: (.*)$")
_INPUT_LINE_RE = re.compile(r"on input line (\d+)")
_BOX_RE = re.compile(r"^(Overfull|Underfull) \\[hv]box .*? (?:at lines? (\d+)(?:--\d+)?|has occurred while \\output is active|detected at line (\d+))")
_CONTEXT_RE = re.compile(r"^l\.(\d+) (.*)$")
_TOKEN_RE = re.compile(r"\(([^\s()]+)|\)")
_PATHLIKE_RE = re.compile(r"^(\./|/|[A-Za-z]:|[\w.-]+/)?[\w./+-]+\.[A-Za-z0-9]{1,8}$")

MAX_DIAGNOSTICS = 300


def _norm(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("./"):
        path = path[2:]
    return path


def _is_project(path: str | None) -> bool:
    return bool(path) and not path.startswith("/") and not path.startswith("_")


class _Stack:
    def __init__(self) -> None:
        self.items: list[str | None] = []

    def feed(self, line: str) -> None:
        for m in _TOKEN_RE.finditer(line):
            if m.group(0) == ")":
                if self.items:
                    self.items.pop()
            else:
                token = m.group(1)
                self.items.append(token if _PATHLIKE_RE.match(token) else None)

    def current(self) -> tuple[str | None, bool]:
        """(nearest project file, whether TeX is currently inside that very file)."""
        top = next((f for f in reversed(self.items) if f), None)
        for f in reversed(self.items):
            if f and _is_project(_norm(f)):
                return _norm(f), f == top
        return None, False


def parse(log: str) -> list[dict]:
    diags: list[dict] = []
    stack = _Stack()
    lines = log.splitlines()
    i = 0
    seen: set[tuple] = set()

    def add(level: str, file: str | None, line: int | None, message: str, context: str | None = None) -> None:
        key = (level, file, line, message)
        if key in seen or len(diags) >= MAX_DIAGNOSTICS:
            return
        seen.add(key)
        diags.append({"level": level, "file": file, "line": line, "message": message.strip(), "context": context})

    while i < len(lines):
        raw = lines[i]
        m = _FILE_ERR_RE.match(raw)
        if m:
            file, line, msg = _norm(m.group(1)), int(m.group(2)), m.group(3)
            context = None
            for j in range(i + 1, min(i + 12, len(lines))):
                cm = _CONTEXT_RE.match(lines[j])
                if cm:
                    context = cm.group(2).strip()
                    break
            if not _is_project(file):
                pf, _ = stack.current()
                msg = f"{msg} (in {file})"
                file, line = pf, None
            add("error", file, line, msg, context)
            i += 1
            continue
        m = _BANG_RE.match(raw)
        if m:
            file, inside = stack.current()
            line = None
            context = None
            for j in range(i + 1, min(i + 12, len(lines))):
                cm = _CONTEXT_RE.match(lines[j])
                if cm:
                    context = cm.group(2).strip()
                    line = int(cm.group(1)) if inside else None
                    break
            add("error", file, line, m.group(1), context)
            i += 1
            continue
        m = _WARN_RE.match(raw)
        if m:
            text = m.group(5)
            pkg = m.group(2) or m.group(3)
            j = i + 1
            # Package warnings continue on lines prefixed with "(pkgname)".
            while pkg and j < len(lines) and lines[j].startswith(f"({pkg})"):
                text += " " + lines[j][len(pkg) + 2 :].strip()
                j += 1
            file, inside = stack.current()
            lm = _INPUT_LINE_RE.search(text)
            line = int(lm.group(1)) if lm and inside else None
            source = m.group(2) or m.group(3) or m.group(1)
            level = "info" if m.group(4) else "warning"  # font warnings are noise
            if "Label(s) may have changed" in text or "Rerun to get" in text or "Shell escape feature is not enabled" in text:
                level = "info"
            add(level, file, line, f"{source}: {text}" if source not in ("LaTeX",) else text)
            stack.feed(raw)
            i = j
            continue
        m = _BOX_RE.match(raw)
        if m:
            file, inside = stack.current()
            ln = m.group(2) or m.group(3)
            add("badbox", file, int(ln) if ln and inside else None, raw.split(" in ")[0].split(" at ")[0])
            i += 1
            continue
        stack.feed(raw)
        i += 1
    return diags


def summary(diags: list[dict]) -> dict:
    return {lvl: sum(1 for d in diags if d["level"] == lvl) for lvl in ("error", "warning", "badbox", "info")}
