"""Semantic retrieval over `index_chunks` (pgvector cosine distance).

`search()` returns None whenever embeddings are off, unavailable in this
process, not benchmarked, too slow (below the configured threshold) or not yet
indexed; callers then fall back to lexical-only retrieval.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import embeddings
from .settings import get_section

# Only the worker loads the model (it stays resident there); the backend never does.
ALLOW_LOAD = False


async def status(db: AsyncSession) -> dict[str, Any]:
    s = await get_section(db, "embeddings")
    if s.model == "off":
        return {"enabled": False, "reason": "embeddings are turned off"}
    if s.model not in embeddings.AVAILABLE:
        return {"enabled": False, "reason": f"model {s.model} is not available in this image"}
    b = s.benchmark or {}
    if not b:
        return {"enabled": False, "reason": "not benchmarked yet"}
    if not b.get("ok"):
        return {"enabled": False, "reason": f"benchmark failed: {b.get('error', 'unknown error')}"}
    if b.get("model") != s.model:
        return {"enabled": False, "reason": "the benchmark was run for another model"}
    if float(b.get("chunks_per_s") or 0) < s.min_chunks_per_s:
        return {"enabled": False, "reason": f"too slow ({b.get('chunks_per_s')} chunks/s < {s.min_chunks_per_s})"}
    return {"enabled": True, "reason": "ok", "model": s.model, "threads": s.threads}


def to_pg(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def normalise(sim: float) -> float:
    """Cosine similarity of this model → 0..1 relevance (unrelated text sits around 0.1–0.3)."""
    return max(0.0, min(1.0, (sim - 0.2) / 0.6))


async def search(db: AsyncSession, query: str, *, course_id: int | None = None, limit: int = 20) -> list[dict[str, Any]] | None:
    st = await status(db)
    if not st["enabled"] or not (ALLOW_LOAD or embeddings.is_loaded()):
        return None
    have = (
        await db.execute(text("SELECT count(*) FROM index_chunks WHERE embedding IS NOT NULL AND model = :m"), {"m": st["model"]})
    ).scalar_one()
    if not have:
        return None
    from ..worker.context import run_cpu

    [vec] = await run_cpu(embeddings.embed, [query[:2000]], st["model"], st["threads"])
    rows = await db.execute(
        text(
            """
            SELECT n.chapter_id, n.course_id, ch.title, max(n.sim) AS sim
            FROM (
                SELECT chapter_id, course_id, 1 - (embedding <=> CAST(:q AS vector)) AS sim
                FROM index_chunks
                WHERE embedding IS NOT NULL AND model = :m AND (CAST(:cid AS integer) IS NULL OR course_id = :cid)
                ORDER BY embedding <=> CAST(:q AS vector)
                LIMIT 300
            ) n JOIN chapters ch ON ch.id = n.chapter_id
            GROUP BY n.chapter_id, n.course_id, ch.title
            ORDER BY sim DESC
            LIMIT :limit
            """
        ),
        {"q": to_pg(vec), "m": st["model"], "cid": course_id, "limit": limit},
    )
    return [
        {"chapter_id": cid, "course_id": course, "chapter_title": title, "semantic": round(normalise(float(sim)), 4), "cosine": round(float(sim), 4)}
        for cid, course, title, sim in rows.all()
    ]
