"""Laboratories: the files of a lesson's lab. What a file is (text, notebook, PDF, picture, anything else) is read from its
bytes and its name; nothing here ever runs a file."""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass

from .sniff import sniff_bytes

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES = 500
MAX_PATH = 300

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
    """Where a comment sits: `{}` (the whole file) or `{from, to, text}` (lines, 1-based, with their text)."""
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
        return {"from": a, "to": b, "text": text[:MAX_ANCHOR_TEXT]}
    raise AnchorError("Posizione del commento non valida")
