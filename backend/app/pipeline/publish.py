"""Publishing: ON/OFF per course. While ON the public site shows the latest version.

Switching ON queues a clean build (review markers stripped); after that every change to the
course (imports, edits, chat) queues a new build once the course has been quiet for a while
(`republish_loop`). A failed build leaves the previous public PDF online.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

import fitz  # PyMuPDF
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..db import SessionLocal
from ..models import Course, Job, Publication
from ..services import blobs, compile as compile_svc, projects, source_export
from ..services import jobs as jobs_svc
from ..worker.context import JobContext, JobFailed, run_cpu
from ..worker.registry import handler

log = logging.getLogger("lecta.publish")


def _norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"^\s*(chapter|capitolo)?\s*[0-9ivxlc]+[.:)]?\s+", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def split_chapters(pdf: bytes, chapters: list[dict]) -> list[dict]:
    """Page ranges per chapter from the PDF outline, plus per-chapter PDFs."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        level1 = [(t, p) for lvl, t, p, *_ in doc.get_toc(simple=False) if lvl == 1 and p > 0]
        starts: list[int | None] = [None] * len(chapters)
        used: set[int] = set()
        for i, ch in enumerate(chapters):
            want = _norm_title(ch["title"])
            for k, (title, page) in enumerate(level1):
                if k not in used and _norm_title(title) == want:
                    starts[i] = page
                    used.add(k)
                    break
        if any(s is None for s in starts) and len(level1) == len(chapters):
            starts = [p for _, p in level1]
        out = []
        known = sorted({s for s in starts if s})
        for ch, start in zip(chapters, starts, strict=True):
            item = dict(ch)
            item["page_start"] = start
            item["pdf_blob"] = None
            item["pdf_size"] = None
            if start:
                later = [s for s in known if s > start]
                end = (later[0] - 1) if later else doc.page_count
                part = fitz.open()
                part.insert_pdf(doc, from_page=start - 1, to_page=end - 1)
                data = part.tobytes(garbage=3, deflate=True)
                part.close()
                item["page_end"] = end
                item["pdf_bytes"] = data
            out.append(item)
        return out
    finally:
        doc.close()


@handler("publish")
async def publish_job(ctx: JobContext) -> dict[str, Any]:
    course_id = int(ctx.payload["course_id"])
    async with SessionLocal() as db:
        course = await db.get(Course, course_id)
        if course is None:
            raise JobFailed("course not found")
        # What this build covers: everything changed up to now (later edits trigger another build).
        course.publish_requested_at = course.updated_at
        await db.commit()
        chapters = await projects.chapters_of(db, course_id)
        if not chapters:
            await ctx.log("the course has no chapters yet: nothing to publish")
            return {"skipped": "no chapters"}
        await ctx.progress(0.1, "clean build (review markers stripped)")
        first = course.current_publication_id is None
        res = await compile_svc.run_build(
            db, course, kind="publish", workdir_name="publish", full=True, priority="background", clean=first, mode="publish",
        )
        if res["status"] == "superseded":
            return {"skipped": "superseded by a newer build"}
        if res["status"] != "ok" or not res.get("pdf"):
            errors = [d for d in res["diagnostics"] if d["level"] == "error"][:5]
            await ctx.log("publish build failed: " + "; ".join(f"{e['file']}:{e['line']}: {e['message']}" for e in errors), "error")
            # Start from a clean build next time.
            await asyncio.to_thread(shutil.rmtree, projects.work_dir(course_id, "publish"), True)
            raise JobFailed("The public PDF could not be rebuilt (the previous version stays online); fix the errors in the editor.")
        pdf = projects.safe_read_output(config.latex_root / res["pdf"])
        if pdf is None:
            raise JobFailed("could not read the built PDF")
        await ctx.progress(0.7, "splitting chapters")
        chapter_meta = [{"slug": c.slug, "title": c.title, "position": i} for i, c in enumerate(chapters, start=1)]
        parts = await run_cpu(split_chapters, pdf, chapter_meta)
        for part in parts:
            data = part.pop("pdf_bytes", None)
            if data:
                part["pdf_blob"] = blobs.put_bytes(data)
                part["pdf_size"] = len(data)
        source = source_export.build_source_zip(await source_export.project_files(db, course, public=True), name=course.name, public=True)
        course = (await db.execute(select(Course).where(Course.id == course_id).with_for_update())).scalar_one()
        pub = (await db.execute(select(Publication).where(Publication.course_id == course_id))).scalar_one_or_none()
        if pub is None:
            pub = Publication(course_id=course_id)
            db.add(pub)
        pub.title, pub.description = course.name, course.description
        pub.pdf_blob, pub.pdf_size, pub.chapters = blobs.put_bytes(pdf), len(pdf), parts
        pub.source_blob, pub.source_size = blobs.put_bytes(source), len(source)
        pub.created_at = datetime.now(UTC)
        await db.flush()
        course.current_publication_id = pub.id
        await db.commit()
    await ctx.log(f"public PDF updated ({len(pdf)} bytes, {len(parts)} chapters)")
    return {"publication_id": pub.id, "chapters": len(parts)}


async def enqueue_publish(db: AsyncSession, course: Course, *, auto: bool) -> Job | None:
    """Queue a publish build unless one is already queued/running for the course."""
    active = (
        await db.execute(select(Job.id).where(Job.kind == "publish", Job.course_id == course.id, Job.status.in_(jobs_svc.ACTIVE)).limit(1))
    ).first()
    if active:
        return None
    return await jobs_svc.enqueue(
        db, "publish", {"course_id": course.id, "auto": auto}, title=f"Pubblicazione di “{course.name}”", priority="publish", course_id=course.id,
    )


_source_backfilled: set[int] = set()


async def republish_pass(quiet_s: int) -> list[int]:
    """Queue a build for every published course changed since its last build and quiet for `quiet_s`."""
    now = datetime.now(UTC)
    queued = []
    async with SessionLocal() as db:
        rows = (await db.execute(select(Course).where(Course.published.is_(True)))).scalars().all()
        for c in rows:
            # Courses published before the source download existed get one rebuild (once per process).
            if c.id not in _source_backfilled and c.current_publication_id:
                pub = await db.get(Publication, c.current_publication_id)
                if pub is not None and pub.source_blob is None:
                    _source_backfilled.add(c.id)
                    if await enqueue_publish(db, c, auto=True):
                        queued.append(c.id)
                    continue
            if c.publish_requested_at is not None and c.updated_at <= c.publish_requested_at:
                continue
            if c.updated_at > now - timedelta(seconds=quiet_s):
                continue
            if await enqueue_publish(db, c, auto=True):
                queued.append(c.id)
    return queued


async def republish_loop(interval: float = 30.0) -> None:
    while True:
        try:
            queued = await republish_pass(config.republish_quiet_s)
            if queued:
                log.info("automatic republish queued for course(s) %s", queued)
        except Exception:
            log.exception("automatic republish failed")
        await asyncio.sleep(interval)


async def prune_auto_publish_jobs(days: int = 1) -> int:
    """Automatic republish jobs that succeeded are clutter after a while."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    async with SessionLocal() as db:
        res = await db.execute(
            delete(Job).where(Job.kind == "publish", Job.status == "succeeded", Job.finished_at < cutoff,
                              Job.payload["auto"].astext == "true")
        )
        await db.commit()
        return res.rowcount or 0
