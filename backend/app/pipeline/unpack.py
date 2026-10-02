"""Safe unpacking of uploaded zips and type detection by magic bytes.

Protections: zip-slip (normalised member names, no absolute paths, no "..",
no symlinks), zip bombs (member count, total uncompressed size and per-member
compression ratio are limited, and sizes are counted while extracting instead
of trusting the headers), nested archives are not expanded, junk is skipped.
"""

from __future__ import annotations

import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..services.sniff import sniff_file

JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini", ".localized"}
JUNK_DIRS = {"__macosx", ".git", ".svn", "__pycache__", ".idea", ".vscode"}
TEXT_EXT = {".md", ".markdown", ".mdown", ".txt", ".tex"}
IMAGE_KINDS = {"png", "jpeg", "heic", "webp", "tiff", "gif"}
CHUNK = 1024 * 1024


class UnpackError(Exception):
    pass


@dataclass
class Member:
    name: str  # basename
    folder: str  # folder inside the archive ("" for top level)
    path: Path | None  # extracted file
    size: int
    kind: str
    status: str = "ok"
    reason: str | None = None


def classify(path: Path, name: str) -> tuple[str, str | None]:
    """(kind, mime) from magic bytes (+ extension only to tell Markdown from plain text)."""
    sniffed = sniff_file(path)
    ext = PurePosixPath(name.lower()).suffix
    if sniffed == "pdf":
        return "pdf", "application/pdf"
    if sniffed in IMAGE_KINDS:
        return "image", f"image/{sniffed}"
    if sniffed == "zip":
        return "zip", "application/zip"
    if sniffed == "text":
        if ext in (".md", ".markdown", ".mdown"):
            return "markdown", "text/markdown"
        if ext in TEXT_EXT or ext == "":
            return "text", "text/plain"
        return "unsupported", "text/plain"
    return "unsupported", None


def is_junk(parts: tuple[str, ...]) -> bool:
    if any(p.lower() in JUNK_DIRS for p in parts[:-1]):
        return True
    base = parts[-1]
    return base.lower() in JUNK_NAMES or base.startswith("._") or base.startswith("~$")


def _safe_parts(name: str) -> tuple[str, ...] | None:
    name = name.replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return None
    parts = tuple(p for p in PurePosixPath(name).parts if p not in ("", "."))
    if not parts or any(p == ".." for p in parts):
        return None
    if any(len(p) > 255 or "\x00" in p for p in parts):
        return None
    return parts


def unpack_zip(
    zip_path: Path,
    dest: Path,
    *,
    max_members: int,
    max_total_bytes: int,
    max_ratio: int,
) -> list[Member]:
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Member] = []
    total = 0
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise UnpackError(f"not a valid zip file: {e}") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > max_members:
            raise UnpackError(f"too many files in the archive ({len(infos)} > {max_members})")
        for n, info in enumerate(infos):
            if info.is_dir():
                continue
            parts = _safe_parts(info.filename)
            if parts is None:
                out.append(Member(info.filename[-200:], "", None, 0, "unsupported", "skipped", "unsafe path in archive"))
                continue
            name, folder = parts[-1], "/".join(parts[:-1])
            if is_junk(parts):
                out.append(Member(name, folder, None, info.file_size, "junk", "skipped", "system file"))
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                out.append(Member(name, folder, None, 0, "unsupported", "skipped", "symbolic link"))
                continue
            if info.flag_bits & 0x1:
                out.append(Member(name, folder, None, info.file_size, "unsupported", "skipped", "encrypted"))
                continue
            if info.compress_size and info.file_size / max(1, info.compress_size) > max_ratio and info.file_size > 10 * CHUNK:
                raise UnpackError(f"suspicious compression ratio for {name} (possible zip bomb)")
            target = dest / f"m{n:05d}"
            written = 0
            try:
                with zf.open(info) as src, open(target, "wb") as dst:
                    while chunk := src.read(CHUNK):
                        written += len(chunk)
                        total += len(chunk)
                        if total > max_total_bytes:
                            raise UnpackError("archive expands beyond the upload limit (possible zip bomb)")
                        if info.compress_size and written > max(info.compress_size, 1) * max_ratio and written > 10 * CHUNK:
                            raise UnpackError(f"{name} expands too much (possible zip bomb)")
                        dst.write(chunk)
            except UnpackError:
                target.unlink(missing_ok=True)
                raise
            except (zipfile.BadZipFile, OSError, RuntimeError) as e:
                target.unlink(missing_ok=True)
                out.append(Member(name, folder, None, 0, "unsupported", "error", f"could not extract: {e}"))
                continue
            kind, _ = classify(target, name)
            status, reason = "ok", None
            if kind == "zip":
                status, reason = "unsupported", "nested archives are not expanded"
            elif kind == "unsupported":
                status, reason = "unsupported", "unsupported file type"
            out.append(Member(name, folder, target, written, kind, status, reason))
    return out


def disk_free(path: Path) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize
