"""Background indexing of chapter content (low priority, incremental).

Every pass looks for chapters whose file changed since it was indexed (or
whose embedding model changed), re-chunks them, keeps chunks whose content hash
is unchanged and embeds only the new ones.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert

from ..db import SessionLocal
from ..models import Chapter, Course, IndexChunk, IndexState, ProjectFile
from ..services import embeddings, semantic, templates
from ..services import settings as settings_svc
from ..worker.context import JobContext, run_cpu
from ..worker.registry import handler

log = logging.getLogger("lecta.indexer")


async def reindex_chapter(chapter: Chapter, course: Course, pf: ProjectFile, model: str | None, threads: int) -> int:
    chunks = embeddings.chunk_chapter(chapter.title, pf.text_content or "")
    tag = model or "lexical"
    wanted = {embeddings.content_hash(c["text"], tag): c for c in chunks}
    async with SessionLocal() as db:
        existing = dict((await db.execute(select(IndexChunk.content_hash, IndexChunk.id).where(IndexChunk.chapter_id == chapter.id))).all())
        stale = [cid for h, cid in existing.items() if h not in wanted]
        if stale:
            await db.execute(delete(IndexChunk).where(IndexChunk.id.in_(stale)))
        new = [(h, c) for h, c in wanted.items() if h not in existing]
        cfg = templates.ts_config(course.language)
        new_ids: list[tuple[int, str]] = []
        for h, c in new:
            row = IndexChunk(course_id=course.id, chapter_id=chapter.id, heading=c["heading"][:500], text=c["text"], content_hash=h, model=model)
            db.add(row)
            await db.flush()
            new_ids.append((row.id, c["text"]))
        if new_ids:
            await db.execute(
                text("UPDATE index_chunks SET tsv = to_tsvector(CAST(:cfg AS regconfig), text) WHERE id = ANY(:ids)"),
                {"cfg": cfg, "ids": [i for i, _ in new_ids]},
            )
        await db.commit()
    if model and new_ids:
        vectors = await run_cpu(embeddings.embed, [t for _, t in new_ids], model, threads)
        async with SessionLocal() as db:
            for (cid, _), vec in zip(new_ids, vectors, strict=True):
                await db.execute(text("UPDATE index_chunks SET embedding = CAST(:v AS vector) WHERE id = :id"), {"v": semantic.to_pg(vec), "id": cid})
            await db.commit()
    async with SessionLocal() as db:
        stmt = insert(IndexState).values(chapter_id=chapter.id, blob=pf.blob, model=model, chunks=len(wanted), indexed_at=datetime.now(UTC))
        stmt = stmt.on_conflict_do_update(
            index_elements=["chapter_id"],
            set_={"blob": stmt.excluded.blob, "model": stmt.excluded.model, "chunks": stmt.excluded.chunks, "indexed_at": stmt.excluded.indexed_at},
        )
        await db.execute(stmt)
        await db.commit()
    return len(new_ids)


async def index_pending(limit: int = 25) -> int:
    async with SessionLocal() as db:
        st = await semantic.status(db)
        s = await settings_svc.get_section(db, "embeddings")
        model = st.get("model") if st["enabled"] else None
        rows = (
            await db.execute(
                select(Chapter, Course, ProjectFile)
                .join(Course, Course.id == Chapter.course_id)
                .join(ProjectFile, (ProjectFile.course_id == Chapter.course_id) & (ProjectFile.path == Chapter.path))
                .outerjoin(IndexState, IndexState.chapter_id == Chapter.id)
                .where(
                    (IndexState.chapter_id.is_(None))
                    | (IndexState.blob != ProjectFile.blob)
                    | (IndexState.model.is_distinct_from(model))
                )
                .limit(limit)
            )
        ).all()
    n = 0
    for chapter, course, pf in rows:
        try:
            n += await reindex_chapter(chapter, course, pf, model, s.threads)
        except Exception:
            log.exception("indexing chapter %s failed", chapter.id)
    return n


async def ensure_benchmark(force: bool = False) -> dict[str, Any] | None:
    async with SessionLocal() as db:
        s = await settings_svc.get_section(db, "embeddings")
    if s.model == "off" or s.model not in embeddings.AVAILABLE:
        return None
    b = s.benchmark or {}
    if not force and b.get("model") == s.model and b.get("threads") == s.threads:
        return b
    try:
        result = await run_cpu(embeddings.benchmark, s.model, s.threads)
    except Exception as e:  # noqa: BLE001
        result = {"model": s.model, "threads": s.threads, "ok": False, "error": str(e)[:300]}
    result["at"] = datetime.now(UTC).isoformat()
    async with SessionLocal() as db:
        await settings_svc.set_section(db, "embeddings", {"benchmark": result})
    log.info("embedding benchmark: %s", result)
    return result


async def loop(interval: float = 30) -> None:
    await ensure_benchmark()
    while True:
        try:
            n = await index_pending()
            if n:
                log.info("indexed %s new chunks", n)
        except Exception:
            log.exception("indexer pass failed")
        await asyncio.sleep(interval)


@handler("embeddings.benchmark")
async def benchmark_job(ctx: JobContext) -> dict[str, Any]:
    res = await ensure_benchmark(force=True)
    await ctx.log(f"benchmark: {res}")
    return res or {"skipped": "embeddings are off"}


@handler("index.rebuild")
async def rebuild_job(ctx: JobContext) -> dict[str, Any]:
    async with SessionLocal() as db:
        await db.execute(delete(IndexState))
        await db.execute(delete(IndexChunk))
        await db.commit()
    total = 0
    while True:
        n = await index_pending(limit=50)
        async with SessionLocal() as db:
            left = (
                await db.execute(
                    text("SELECT count(*) FROM chapters c LEFT JOIN index_state s ON s.chapter_id = c.id WHERE s.chapter_id IS NULL")
                )
            ).scalar_one()
        total += n
        await ctx.progress(0.5 if left else 1.0, f"{total} chunks embedded, {left} chapters left")
        if not left:
            break
    return {"chunks": total}
