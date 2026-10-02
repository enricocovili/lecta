"""Content-addressed blob store (sha256) under DATA_DIR/blobs."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from ..config import config

CHUNK = 1024 * 1024


def root() -> Path:
    return config.data_dir / "blobs"


def path_for(h: str) -> Path:
    if len(h) != 64 or not all(c in "0123456789abcdef" for c in h):
        raise ValueError("invalid blob hash")
    return root() / h[:2] / h[2:4] / h


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put_bytes(data: bytes) -> str:
    h = sha256_bytes(data)
    p = path_for(h)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{h}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
    return h


def put_file(src: Path, *, move: bool = False) -> str:
    """Hash a file in streaming fashion and store it (never loads it whole)."""
    hasher = hashlib.sha256()
    with open(src, "rb") as f:
        while chunk := f.read(CHUNK):
            hasher.update(chunk)
    h = hasher.hexdigest()
    p = path_for(h)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{h}.{os.getpid()}.tmp")
        if move:
            shutil.move(src, tmp)
        else:
            shutil.copyfile(src, tmp)
        os.replace(tmp, p)
    elif move:
        src.unlink(missing_ok=True)
    return h


def read_bytes(h: str) -> bytes:
    return path_for(h).read_bytes()


def read_text(h: str) -> str:
    return path_for(h).read_text(encoding="utf-8", errors="replace")


def exists(h: str) -> bool:
    try:
        return path_for(h).exists()
    except ValueError:
        return False
