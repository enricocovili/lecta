"""Laboratories: the files of a lesson's lab. What a file is (text, notebook, PDF, picture, anything else) is read from its
bytes and its name; nothing here ever runs a file."""

from __future__ import annotations

import difflib
import json
import posixpath
import re
from dataclasses import dataclass

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LabComment, LabFile
from .sniff import sniff_bytes

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES = 500
MAX_PATH = 300
MAX_NOTES_CHARS = 200_000

# Extension (or whole file name) -> language, for the colours of the viewer and for the assistant.
LANGUAGES: dict[str, str] = {
    "c": "c", "h": "c", "cpp": "cpp", "cc": "cpp", "cxx": "cpp", "hpp": "cpp", "hh": "cpp", "hxx": "cpp", "ino": "cpp",
    "cs": "csharp", "java": "java", "kt": "kotlin", "kts": "kotlin", "scala": "scala", "go": "go", "rs": "rust",
    "swift": "swift", "py": "python", "pyw": "python", "pyi": "python", "ipynb": "python", "r": "r", "rmd": "markdown",
    "jl": "julia", "m": "matlab", "mat": "matlab", "js": "javascript", "mjs": "javascript", "cjs": "javascript",
    "jsx": "javascript", "ts": "typescript", "tsx": "typescript", "php": "php", "rb": "ruby", "pl": "perl", "lua": "lua",
    "hs": "haskell", "ml": "ocaml", "erl": "erlang", "ex": "elixir", "exs": "elixir", "clj": "clojure", "lisp": "lisp",
    "scm": "scheme", "rkt": "scheme", "f": "fortran", "f90": "fortran", "f95": "fortran", "pas": "pascal",
    "s": "asm", "asm": "asm", "v": "verilog", "sv": "verilog", "vhd": "vhdl", "vhdl": "vhdl",
    "sql": "sql", "sh": "shell", "bash": "shell", "zsh": "shell", "ps1": "powershell", "bat": "batch", "cmd": "batch",
    "html": "html", "htm": "html", "xml": "xml", "svg": "xml", "xsd": "xml", "css": "css", "scss": "css",
    "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml", "ini": "ini", "cfg": "ini", "conf": "ini",
    "md": "markdown", "markdown": "markdown", "tex": "latex", "sty": "latex", "bib": "latex", "csv": "csv",
    "proto": "protobuf", "cmake": "cmake", "gradle": "groovy", "groovy": "groovy", "dart": "dart", "vue": "html",
    "txt": "text", "log": "text",
}
NAMES: dict[str, str] = {"makefile": "makefile", "gnumakefile": "makefile", "dockerfile": "dockerfile", "cmakelists.txt": "cmake"}

IMAGES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}

_BAD = re.compile(r"[\x00-\x1f\x7f\\:*?\"<>|]")


class PathError(ValueError):
    pass


def clean_path(raw: str) -> str:
    """A file's name in the lab, with folders: "src/main.c". No absolute paths, no `..`, no hidden or odd characters."""
    p = (raw or "").strip().replace("\\", "/")
    parts = [s.strip() for s in p.split("/") if s.strip() not in ("", ".")]
    if not parts:
        raise PathError("Nome del file vuoto")
    if any(s == ".." or _BAD.search(s) for s in parts):
        raise PathError("Nome del file non valido")
    out = posixpath.join(*parts)
    if len(out) > MAX_PATH:
        raise PathError(f"Nome del file troppo lungo (al massimo {MAX_PATH} caratteri)")
    return out


def language_for(path: str) -> str | None:
    name = posixpath.basename(path).lower()
    if name in NAMES:
        return NAMES[name]
    ext = name.rsplit(".", 1)[1] if "." in name else ""
    return LANGUAGES.get(ext)


def decode_text(data: bytes) -> str | None:
    """The text of a file, or None if it is binary. UTF-8 (with or without BOM), else Windows-1252 / Latin-1 as a teacher's
    old files often are; a NUL byte means binary."""
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


@dataclass
class Classified:
    kind: str  # text | notebook | pdf | image | binary
    language: str | None
    content: str | None  # text and notebooks


def classify(path: str, data: bytes) -> Classified:
    """What a file is, from its bytes first (magic numbers), then its name."""
    sniffed = sniff_bytes(data[:16])
    if sniffed == "pdf":
        return Classified("pdf", None, None)
    if sniffed in IMAGES:
        return Classified("image", None, None)
    if sniffed is not None:  # zip, archives, other pictures: kept, downloadable, not shown
        return Classified("binary", None, None)
    text = decode_text(data)
    if text is None:
        return Classified("binary", None, None)
    text = text.replace("\r\n", "\n")
    lang = language_for(path)
    if path.lower().endswith(".ipynb"):
        try:
            nb = json.loads(text)
            if isinstance(nb, dict) and isinstance(nb.get("cells"), list):
                kernel = ((nb.get("metadata") or {}).get("language_info") or {}).get("name")
                return Classified("notebook", (str(kernel).lower()[:30] if kernel else lang), text)
        except ValueError:
            pass
    return Classified("text", lang or "text", text)


def media_type(kind: str, blob_head: bytes | None) -> str:
    """The type a stored file is served with: only pictures and PDFs recognised by their bytes get their own; the rest is opaque."""
    if kind in ("pdf", "image") and blob_head is not None:
        sniffed = sniff_bytes(blob_head[:16])
        if sniffed == "pdf":
            return "application/pdf"
        if sniffed in IMAGES:
            return IMAGES[sniffed]
    return "application/octet-stream"


# --------------------------------------------------------------------------- comments

MAX_COMMENT_CHARS = 20_000
MAX_COMMENTS = 5000
MAX_ANCHOR_TEXT = 4000
COMMENT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class AnchorError(ValueError):
    pass


def clean_anchor(raw: object) -> dict:
    """Where a comment sits: `{}` (the whole file), `{from, to, text}` (lines, 1-based, with their text), `{cell}` (a cell of a
    notebook) or `{page}` (a page of a PDF)."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise AnchorError("Posizione del commento non valida")
    if "from" in raw:
        a, b = raw.get("from"), raw.get("to", raw.get("from"))
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (a, b)) or not 1 <= a <= b <= 10_000_000:
            raise AnchorError("Righe del commento non valide")
        text = raw.get("text", "")
        if not isinstance(text, str):
            raise AnchorError("Testo delle righe non valido")
        out: dict = {"from": a, "to": b, "text": text[:MAX_ANCHOR_TEXT]}
        if raw.get("gone") is True:  # its lines were removed by an edit: the comment stays on the file
            out["gone"] = True
        return out
    for key in ("cell", "page"):  # a cell of a notebook, a page of a PDF (1-based)
        if key in raw:
            n = raw[key]
            if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 100_000:
                raise AnchorError("Posizione del commento non valida")
            return {key: n}
    raise AnchorError("Posizione del commento non valida")


def remap_anchor(anchor: dict, old: str, new: str, matcher: difflib.SequenceMatcher | None = None) -> dict:
    """Where a comment's lines are after the file changed from `old` to `new` (a new upload, an edit of the assistant): the lines
    that survive unchanged carry it; if none does, it is kept as `gone` with the text it had."""
    if "from" not in anchor or anchor.get("gone"):
        return anchor
    old_lines, new_lines = old.split("\n"), new.split("\n")
    sm = matcher or difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    moved: dict[int, int] = {}
    for tag, i1, i2, j1, _ in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                moved[i1 + k] = j1 + k
    kept = [moved[i] for i in range(anchor["from"] - 1, anchor["to"]) if i in moved]
    if not kept:
        return {**anchor, "gone": True}
    a, b = min(kept), max(kept)
    return {"from": a + 1, "to": b + 1, "text": "\n".join(new_lines[a : b + 1])[:MAX_ANCHOR_TEXT]}


def remap_anchors(anchors: list[dict], old: str, new: str) -> list[dict]:
    sm = difflib.SequenceMatcher(None, old.split("\n"), new.split("\n"), autojunk=False)
    return [remap_anchor(a, old, new, sm) for a in anchors]


async def follow_comments(db: AsyncSession, f: LabFile, old: str, new: str) -> None:
    """The comments on a text file follow their lines when its text changes (a new upload, the assistant, an undo)."""
    if old == new:
        return
    comments = [c for c in (await db.execute(select(LabComment).where(LabComment.file_id == f.id))).scalars() if "from" in (c.anchor or {})]
    if not comments:
        return
    for c, anchor in zip(comments, remap_anchors([c.anchor for c in comments], old, new), strict=True):
        if anchor != c.anchor:
            c.anchor = anchor
            c.version += 1
            c.updated_at = datetime.now(UTC)


# --------------------------------------------------------------------------- comments into the lesson's text

# A comment brings at most this many lines of its code into the notes (the writer quotes code only when it must anyway).
MAX_COMMENT_CODE_LINES = 60


def _fence(code: str, language: str | None) -> str:
    ticks = "```"
    while ticks in code:
        ticks += "`"
    return f"{ticks}{language or ''}\n{code.rstrip()}\n{ticks}"


def _cell_source(content: str | None, n: int) -> str:
    try:
        cells = json.loads(content or "").get("cells") or []
        src = cells[n - 1].get("source", "")
    except (ValueError, AttributeError, IndexError, TypeError):
        return ""
    return "".join(src) if isinstance(src, list) else str(src)


def _cut(code: str) -> str:
    lines = code.splitlines()
    return "\n".join(lines[:MAX_COMMENT_CODE_LINES] + (["…"] if len(lines) > MAX_COMMENT_CODE_LINES else []))


def _where(c: LabComment) -> tuple[int, int, str]:
    a = c.anchor or {}
    if "from" in a:
        return (2 if a.get("gone") else 1, a["from"], "")
    for key in ("cell", "page"):
        if key in a:
            return (1, a[key], "")
    return (0, 0, "")


def comments_markdown(files: list[LabFile], comments: list[LabComment]) -> str | None:
    """The lab's comments as a section of the lesson's notes («Commenti del laboratorio»): one heading per comment with its file
    and place, the code it is about (the lines, the notebook cell), then what the student wrote. None without comments."""
    by_id = {f.id: f for f in files}
    out: list[str] = []
    for c in sorted(comments, key=lambda c: (by_id[c.file_id].path if c.file_id in by_id else "", _where(c), c.created_at)):
        f = by_id.get(c.file_id)
        body = (c.body or "").strip()
        if f is None or not body:
            continue
        a = c.anchor or {}
        code = ""
        if "from" in a:
            lines = (f.content or "").splitlines()
            if a.get("gone") or not lines:
                place, code = "righe rimosse", a.get("text") or ""
            else:
                place = f"riga {a['from']}" if a["from"] == a["to"] else f"righe {a['from']}–{a['to']}"
                code = "\n".join(lines[a["from"] - 1 : a["to"]]) or a.get("text") or ""
        elif "cell" in a:
            place, code = f"cella {a['cell']}", _cell_source(f.content, a["cell"])
        elif "page" in a:
            place = f"pagina {a['page']}"
        else:
            place = "tutto il file"
        part = f"### {f.path}, {place}\n\n"
        if code.strip():
            part += _fence(_cut(code), f.language) + "\n\n"
        out.append(part + body)
    if not out:
        return None
    return "## Commenti del laboratorio\n\n" + "\n\n".join(out) + "\n"


async def lesson_comments_markdown(db: AsyncSession, lab_id: int) -> str | None:
    files = list((await db.execute(select(LabFile).where(LabFile.lab_id == lab_id))).scalars())
    comments = list((await db.execute(select(LabComment).where(LabComment.lab_id == lab_id))).scalars())
    return comments_markdown(files, comments)
