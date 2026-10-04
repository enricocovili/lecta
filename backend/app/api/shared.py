"""A lesson through a share link: no sign-in, the secret is in the link (/s/<token>). A «read» link shows the slides, the notes and the
strokes; a «write» link also lets the visitor write notes and draw, add and remove pages, exactly like the owner's editor.
The routes mirror the owner's (`/lessons/{id}/…`) so the same editor works on both; what only the owner does (generating the text,
sharing, renaming, moving, deleting the lesson) is not here."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Course, Lesson, LessonShare
from . import lessons as owner

router = APIRouter(prefix="/public/lesson/{token}")

TOUCH_EVERY = timedelta(minutes=5)


async def _open(db: AsyncSession, token: str, write: bool = False) -> tuple[Lesson, LessonShare]:
    sh = (await db.execute(select(LessonShare).where(LessonShare.token == token))).scalar_one_or_none()
    lesson = await db.get(Lesson, sh.lesson_id) if sh else None
    if sh is None or lesson is None:
        raise HTTPException(status_code=404, detail="Not Found")
    if write and sh.mode != "write":
        raise HTTPException(status_code=403, detail="Questo link è in sola lettura")
    now = datetime.now(UTC)
    if sh.last_used_at is None or now - sh.last_used_at > TOUCH_EVERY:
        sh.last_used_at = now
        await db.commit()
    return lesson, sh


@router.get("")
async def shared_lesson(token: str, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    response.headers["cache-control"] = "no-store"
    lesson, sh = await _open(db, token)
    course = await db.get(Course, lesson.course_id)
    return {
        "mode": sh.mode, "title": lesson.title, "course_name": course.name if course else "", "has_pdf": bool(lesson.pdf_blob),
        "pdf_pages": lesson.pdf_pages, "pages": [owner.page_out(p) for p in await owner._pages(db, lesson.id)],
    }


@router.post("/sync")
async def shared_sync(token: str, body: owner.SyncIn, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    response.headers["cache-control"] = "no-store"
    lesson, _ = await _open(db, token)
    return await owner.sync_out(db, lesson, body.known)


@router.get("/pdf")
async def shared_pdf(token: str, db: AsyncSession = Depends(get_db)):
    lesson, _ = await _open(db, token)
    return await owner.slides_pdf(lesson.id, db)


@router.get("/annotated.pdf")
async def shared_annotated(token: str, db: AsyncSession = Depends(get_db)):
    lesson, _ = await _open(db, token)
    return await owner.annotated_pdf(lesson.id, db)


@router.get("/notes.md")
async def shared_notes(token: str, db: AsyncSession = Depends(get_db)):
    lesson, _ = await _open(db, token)
    return await owner.notes_md(lesson.id, db)


@router.put("/pages/{page_id}")
async def shared_save(token: str, page_id: int, body: owner.PageSave, db: AsyncSession = Depends(get_db)) -> dict:
    lesson, _ = await _open(db, token, write=True)
    return await owner.save_page(lesson.id, page_id, body, db)


@router.post("/pages", status_code=201)
async def shared_add(token: str, body: owner.PageAdd, db: AsyncSession = Depends(get_db)) -> dict:
    lesson, _ = await _open(db, token, write=True)
    return await owner.add_page(lesson.id, body, db)


@router.post("/pages/restore", status_code=201)
async def shared_restore(token: str, body: owner.PageRestore, db: AsyncSession = Depends(get_db)) -> dict:
    lesson, _ = await _open(db, token, write=True)
    return await owner.restore_page(lesson.id, body, db)


@router.delete("/pages/{page_id}")
async def shared_delete(token: str, page_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    lesson, _ = await _open(db, token, write=True)
    return await owner.delete_page(lesson.id, page_id, db)
