"""Local embeddings (fastembed, quantised ONNX, CPU only).

The model is loaded once in the worker and stays resident; ONNX threads are
capped from Settings. Model files are baked into the image at build time
(HF_HUB_OFFLINE=1 at runtime), so nothing is downloaded while running.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from typing import Any

from ..config import config
from .texttools import latex_to_text

log = logging.getLogger("lecta.embeddings")

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
AVAILABLE = [DEFAULT_MODEL]
DIM = 384
WINDOW_WORDS = 60  # ≈ 100 tokens for this model's multilingual word-pieces (max input 128)
OVERLAP_WORDS = 12

_lock = threading.Lock()
_model: Any = None
_model_key: tuple[str, int] | None = None


def load(model_name: str, threads: int) -> Any:
    global _model, _model_key
    with _lock:
        if _model is not None and _model_key == (model_name, threads):
            return _model
        from fastembed import TextEmbedding

        t0 = time.monotonic()
        _model = TextEmbedding(model_name, cache_dir=str(config.model_dir), threads=threads, local_files_only=True)
        _model_key = (model_name, threads)
        log.info("loaded embedding model %s (threads=%s) in %.1fs", model_name, threads, time.monotonic() - t0)
        return _model


def is_loaded() -> bool:
    return _model is not None


def embed(texts: list[str], model_name: str, threads: int) -> list[list[float]]:
    m = load(model_name, threads)
    return [v.tolist() for v in m.embed(texts, batch_size=16)]


def chunk_chapter(chapter_title: str, source: str) -> list[dict[str, str]]:
    """Split a chapter into ~100-token windows, each prefixed with its heading."""
    out: list[dict[str, str]] = []
    heading = chapter_title
    sections: list[tuple[str, str]] = []
    buf: list[str] = []
    for line in source.split("\n"):
        m = re.match(r"\s*\\(chapter|section|subsection|subsubsection)\*?\s*(\[[^\]]*\])?\s*\{([^{}]*)\}", line)
        if m:
            if buf:
                sections.append((heading, "\n".join(buf)))
                buf = []
            heading = chapter_title if m.group(1) == "chapter" else f"{chapter_title} / {m.group(3).strip()}"
            continue
        buf.append(line)
    if buf:
        sections.append((heading, "\n".join(buf)))
    for head, body in sections:
        words = latex_to_text(body).split()
        if not words:
            continue
        step = WINDOW_WORDS - OVERLAP_WORDS
        for i in range(0, max(1, len(words) - OVERLAP_WORDS), step):
            window = " ".join(words[i : i + WINDOW_WORDS])
            if len(window) < 20:
                continue
            text = f"{head}: {window}"
            out.append({"heading": head, "text": text})
    return out


def content_hash(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


BENCH_TEXTS = [
    "Un sistema lineare tempo-invariante è completamente caratterizzato dalla sua risposta all'impulso h(t); "
    "l'uscita si ottiene come convoluzione tra ingresso e risposta all'impulso.",
    "The entropy of an isolated system never decreases; in a reversible process it stays constant and the "
    "change in entropy equals the heat exchanged divided by the absolute temperature.",
    "Una matrice quadrata è invertibile se e solo se il suo determinante è diverso da zero; in tal caso "
    "l'inversa si calcola con la matrice dei cofattori.",
    "A group is a set with an associative binary operation, an identity element and inverses for every element.",
] * 8


def benchmark(model_name: str, threads: int) -> dict[str, Any]:
    t0 = time.monotonic()
    load(model_name, threads)
    load_s = time.monotonic() - t0
    embed(BENCH_TEXTS[:2], model_name, threads)  # warm-up
    t1 = time.monotonic()
    vecs = embed(BENCH_TEXTS, model_name, threads)
    dt = max(1e-6, time.monotonic() - t1)
    return {
        "model": model_name,
        "threads": threads,
        "chunks_per_s": round(len(vecs) / dt, 2),
        "load_s": round(load_s, 2),
        "dim": len(vecs[0]),
        "ok": len(vecs[0]) == DIM,
    }
