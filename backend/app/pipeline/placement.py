"""Placement: decide which chapter of the lesson's course new notes go to (an existing one or a new one)."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import Chapter, Course
from ..services import projects, retrieval
from ..services import settings as settings_svc
from ..services.texttools import latex_to_text
from ..worker.context import JobContext, JobFailed
from .common import ai
from .requests import RequestBuilder, dumps


async def decide(
    ctx: JobContext, bundle: dict[str, Any], *, target_course_id: int | None, target_chapter_id: int | None, force_new: bool = False
) -> dict[str, Any]:
    """One outcome: {"type": "merge", course_id, chapter_id} (append to that chapter) | {"type": "new_chapter", course_id, title}.
    The material always comes with its course (a lesson's); a chosen chapter wins."""
    if target_course_id is None and target_chapter_id is None:
        raise JobFailed("material without a course: an import always comes from a lesson of a course")
    if target_chapter_id:
        async with SessionLocal() as db:
            ch = await db.get(Chapter, target_chapter_id)
        return {"type": "merge", "chapter_id": target_chapter_id, "course_id": ch.course_id if ch else target_course_id,
                "confidence": 1.0, "rationale": "chapter chosen for this lesson"}
    new_chapter = {"type": "new_chapter", "course_id": target_course_id, "title": bundle["title"], "confidence": 1.0}
    if force_new:
        return {**new_chapter, "rationale": "a new chapter was asked for (one lesson, one chapter)"}
    async with SessionLocal() as db:
        cat = await settings_svc.get_section(db, "categorization")
        if not await projects.chapters_of(db, target_course_id):
            return {**new_chapter, "rationale": "the course has no chapters yet"}
        cands, mode = await retrieval.candidates(
            db, bundle["body"], title=bundle["title"], course_id=target_course_id, top_k=cat.top_k
        )
    await ctx.log(f"placement candidates for “{bundle['title']}” ({mode}): " + ", ".join(
        f"{c['course_name']}/{c.get('chapter_title') or '—'} {c['score']:.2f}" for c in cands[:5]) or "none")
    if not cands:
        return {**new_chapter, "rationale": "nothing similar in the course"}

    plain = latex_to_text(bundle["body"])[:5000]
    async with SessionLocal() as db:
        rb = RequestBuilder("classify.place")
        rb.instr("The material belongs to one specific course: choose a chapter of it or a new chapter.")
        rb.data(dumps({"title": bundle["title"], "outline": bundle.get("outline") or [], "text": plain}), "the new notes")
        rb.data(
            dumps([
                {"course_id": c["course_id"], "course": c["course_name"], "chapter_id": c.get("chapter_id"), "chapter": c.get("chapter_title"),
                 "outline": c.get("outline") or [], "retrieval_score": c["score"]}
                for c in cands
            ]),
            "candidate chapters (from your library)",
        )
        req = await rb.build(
            db, role="classification", task="classify.place", request_key=f"{ctx.job_id}:place:{bundle['group_key']}",
            meta={"group_text": plain, "group_title": bundle["title"], "candidates": cands}, max_tokens=2000,
        )
    res = await ai(ctx, req, title=f"Collocazione: {bundle['title']}")
    data = res.data if isinstance(res.data, dict) else {}
    valid: list[dict[str, Any]] = []
    async with SessionLocal() as db:
        for pl in data.get("placements") or []:
            try:
                conf = float(pl.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            if not pl.get("course_id") or int(pl["course_id"]) != target_course_id:
                continue
            ch = await db.get(Chapter, int(pl["chapter_id"])) if pl.get("chapter_id") else None
            if ch is not None and ch.course_id != target_course_id:
                ch = None
            item = {"course_id": target_course_id, "confidence": conf, "rationale": str(pl.get("rationale") or "")[:500]}
            if ch is not None:
                valid.append({**item, "type": "merge", "chapter_id": ch.id})
            else:
                valid.append({**item, "type": "new_chapter", "title": str(pl.get("new_chapter_title") or bundle["title"])[:200]})
    best = max(valid, key=lambda v: v["confidence"], default=None)
    return best or {**new_chapter, "confidence": 0.0, "rationale": "no confident match inside the course"}


async def course_language(db: AsyncSession, course_id: int | None) -> str | None:
    if not course_id:
        return None
    c = await db.get(Course, course_id)
    return c.language if c else None


_WS = re.compile(r"\s+")
