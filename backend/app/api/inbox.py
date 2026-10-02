"""Da smistare (not a course): imported notes that couldn't be placed confidently."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Chapter, Course, IngestFigure, IngestItem, InboxItem, Job
from ..security.auth import require_admin
from ..services import jobs as jobs_svc
from ..services import projects
from ..services.texttools import detect_language
from .uploads import item_out

router = APIRouter(dependencies=[Depends(require_admin)])


async def _guesses_out(db: AsyncSession, guesses: list[dict]) -> list[dict]:
    out = []
    for g in guesses:
        c = await db.get(Course, g.get("course_id")) if g.get("course_id") else None
        ch = await db.get(Chapter, g.get("chapter_id")) if g.get("chapter_id") else None
        out.append({**g, "course_name": c.name if c else None, "chapter_title": ch.title if ch else None})
    return out


def _summary(i: InboxItem) -> dict[str, Any]:
    b = i.bundle or {}
    return {
        "id": i.id, "status": i.status, "title": i.title, "language": i.language, "job_id": i.job_id,
        "assigned_job_id": i.assigned_job_id, "created_at": i.created_at, "decided_at": i.decided_at,
        "sections": len(b.get("outline") or []), "figures": len(b.get("images") or {}),
        "source_file_ids": i.source_file_ids,
    }


async def _item(db: AsyncSession, iid: int) -> InboxItem:
    i = await db.get(InboxItem, iid)
    if i is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return i


@router.get("/inbox")
async def list_inbox(status: str = Query("open", max_length=20), db: AsyncSession = Depends(get_db)) -> list[dict]:
    q = select(InboxItem).order_by(InboxItem.id.desc()).limit(300)
    if status != "all":
        q = q.where(InboxItem.status == status)
    out = []
    for i in (await db.execute(q)).scalars():
        d = _summary(i)
        d["guesses"] = await _guesses_out(db, (i.guesses or [])[:1])
        out.append(d)
    return out


@router.get("/inbox/{iid}")
async def get_inbox_item(iid: int, db: AsyncSession = Depends(get_db)) -> dict:
    i = await _item(db, iid)
    out = _summary(i)
    b = i.bundle or {}
    out["guesses"] = await _guesses_out(db, i.guesses or [])
    out["body"] = b.get("body")
    out["outline"] = b.get("outline")
    figs = []
    if i.job_id and b.get("images"):
        rows = (await db.execute(select(IngestFigure).where(IngestFigure.job_id == i.job_id, IngestFigure.key.in_(list(b["images"]))))).scalars()
        figs = [{"key": f.key, "origin": f.origin, "crop_url": f"/api/blobs/figures/{f.id}/crop"} for f in rows]
    out["figures"] = figs
    items = []
    if i.job_id:
        rows = (
            await db.execute(
                select(IngestItem).where(IngestItem.job_id == i.job_id, IngestItem.group_key == b.get("group_key")).order_by(IngestItem.position)
            )
        ).scalars()
        items = [item_out(x) for x in rows]
    out["source_items"] = items
    if i.assigned_job_id:
        j = await db.get(Job, i.assigned_job_id)
        out["assigned_job"] = {"id": j.id, "status": j.status} if j else None
    return out


class AssignIn(BaseModel):
    course_id: int
    chapter_id: int | None = None
    new_chapter_title: str | None = Field(None, max_length=200)


@router.post("/inbox/{iid}/assign")
async def assign(iid: int, body: AssignIn, db: AsyncSession = Depends(get_db)) -> dict:
    i = await _item(db, iid)
    if i.status != "open":
        raise HTTPException(status_code=409, detail=f"item is {i.status}")
    course = await db.get(Course, body.course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Not Found")
    if body.chapter_id:
        ch = await db.get(Chapter, body.chapter_id)
        if ch is None or ch.course_id != course.id:
            raise HTTPException(status_code=404, detail="Not Found")
    job = await jobs_svc.enqueue(
        db, "inbox.assign", {"inbox_id": i.id, "course_id": course.id, "chapter_id": body.chapter_id, "new_chapter_title": body.new_chapter_title},
        title=f"Place “{i.title}” in {course.name}", priority="ingest", course_id=course.id, commit=False,
    )
    i.status = "assigning"
    i.assigned_job_id = job.id
    await db.commit()
    return {"job_id": job.id}


class NewCourseIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    academic_year: str | None = Field(None, max_length=20)
    language: str | None = Field(None, pattern=r"^[a-z]{2,3}$")


@router.post("/inbox/{iid}/new-course")
async def new_course(iid: int, body: NewCourseIn, db: AsyncSession = Depends(get_db)) -> dict:
    i = await _item(db, iid)
    if i.status != "open":
        raise HTTPException(status_code=409, detail=f"item is {i.status}")
    # The course language is detected from the material (can be changed later in the course settings).
    lang = body.language or i.language or detect_language((i.bundle or {}).get("body", ""), default="it")
    if lang == "und":
        lang = "it"
    course = await projects.create_course(db, name=body.name, academic_year=body.academic_year, language=lang)
    await db.commit()
    job = await jobs_svc.enqueue(
        db, "inbox.assign", {"inbox_id": i.id, "course_id": course.id, "chapter_id": None},
        title=f"Place “{i.title}” in {course.name}", priority="ingest", course_id=course.id, commit=False,
    )
    i.status = "assigning"
    i.assigned_job_id = job.id
    await db.commit()
    return {"course_id": course.id, "job_id": job.id, "language": lang}


@router.post("/inbox/{iid}/discard")
async def discard(iid: int, db: AsyncSession = Depends(get_db)) -> dict:
    i = await _item(db, iid)
    if i.status not in ("open",):
        raise HTTPException(status_code=409, detail=f"item is {i.status}")
    i.status = "discarded"
    i.decided_at = datetime.now(UTC)
    await db.commit()
    return {"ok": True}


@router.post("/inbox/{iid}/reopen")
async def reopen(iid: int, db: AsyncSession = Depends(get_db)) -> dict:
    i = await _item(db, iid)
    if i.status not in ("discarded", "assigning"):
        raise HTTPException(status_code=409, detail=f"item is {i.status}")
    if i.status == "assigning" and i.assigned_job_id:
        j = await db.get(Job, i.assigned_job_id)
        if j and j.status in jobs_svc.ACTIVE:
            raise HTTPException(status_code=409, detail="lo smistamento è ancora in corso")
    i.status = "open"
    await db.commit()
    return {"ok": True}
