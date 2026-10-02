"""File type detection by magic bytes (never by extension or client MIME type)."""

from __future__ import annotations

from pathlib import Path


def sniff_bytes(head: bytes) -> str | None:
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:4] == b"PK\x03\x04" or head[:4] == b"PK\x05\x06":
        return "zip"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif"):
            return "heic" if brand != b"avif" else "avif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:6] == b"7z\xbc\xaf'\x1c" or head[:4] == b"Rar!":
        return "archive"
    return None


def looks_like_text(data: bytes) -> bool:
    if not data:
        return True
    if b"\x00" in data[:8192]:
        return False
    try:
        data[:65536].decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        # A multi-byte char cut at the sample boundary is fine.
        return e.start > len(data[:65536]) - 4


def sniff_file(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(65536)
    kind = sniff_bytes(head)
    if kind:
        return kind
    if looks_like_text(head):
        return "text"
    return "unknown"
