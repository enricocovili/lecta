"""Publishing (admin side): ON / OFF per course, and the state of the public PDF."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Course, Job, Publication
from ..pipeline.publish import enqueue_publish
from ..security.auth import require_admin
from ..services import jobs as jobs_svc
from .courses import load_course

router = APIRouter(dependencies=[Depends(require_admin)])


async def publish_state(db: AsyncSession, c: Course) -> dict[str, Any]:
    """off | preparing (first build) | updating | online | failed (the previous version stays online)."""
    pub = await db.get(Publication, c.current_publication_id) if c.current_publication_id else None
    last = (
        await db.execute(select(Job).where(Job.kind == "publish", Job.course_id == c.id).order_by(Job.id.desc()).limit(1))
    ).scalar_one_or_none()
    active = last is not None and last.status in jobs_svc.ACTIVE
    pending = c.published and (c.publish_requested_at is None or c.updated_at > c.publish_requested_at)
    if not c.published:
        state = "off"
    elif active or (pending and not (last is not None and last.status == "failed")):
        state = "updating" if pub else "preparing"
    elif last is not None and last.status == "failed" and (pub is None or (last.finished_at and last.finished_at > pub.created_at)):
        state = "failed"
    elif pub is None:
        state = "preparing"
    else:
        state = "online"
    return {
        "published": c.published,
        "state": state,
        "updated_at": pub.created_at if pub else None,
        "pdf_size": pub.pdf_size if pub else None,
        "error": last.error.splitlines()[0][:300] if state == "failed" and last is not None and last.error else None,
        "job_id": last.id if last is not None and (active or state == "failed") else None,
    }


@router.get("/courses/{course_id}/publish")
async def get_publish(course_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    return await publish_state(db, c)


@router.post("/courses/{course_id}/publish")
async def publish(course_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Switch ON (or retry): the course is public from now on and a build is queued."""
    c = await load_course(db, course_id)
    c.published = True
    await db.commit()
    job = await enqueue_publish(db, c, auto=False)
    await db.commit()
    return {**await publish_state(db, c), "job_id": job.id if job else None}


@router.post("/courses/{course_id}/unpublish")
async def unpublish(course_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    c.published = False
    await db.commit()
    return await publish_state(db, c)
