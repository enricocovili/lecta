"""A lesson through a share link: no sign-in, the secret is in the link (/s/<token>). A «read» link shows the slides, the notes and the
strokes; a «write» link also lets the visitor write notes and draw, add and remove pages, exactly like the owner's editor.
The routes mirror the owner's (`/lessons/{id}/…`) so the same editor works on both; what only the owner does (generating the text,
sharing, renaming, moving, deleting the lesson) is not here. The lesson's lab comes with it (`/lab…`): read links see its files,
comments and notes; write links also comment, write the notes and edit text files, but don't upload, rename or delete files
and have no assistant."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Course, Lab, Lesson, LessonShare
from . import labs as owner_labs
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
        "has_lab": (await db.execute(select(Lab.id).where(Lab.lesson_id == lesson.id))).scalar_one_or_none() is not None,
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


# --------------------------------------------------------------------------- the lesson's lab


@router.get("/lab")
async def shared_lab(token: str, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    """The lab as a link shows it: files, comments, notes; nothing of the owner's side (ids of the lab, lesson, course, chapter)."""
    response.headers["cache-control"] = "no-store"
    lesson, sh = await _open(db, token)
    out = await owner_labs.lab_out(db, await owner_labs._lab(db, lesson.id))
    course = await db.get(Course, lesson.course_id)
    out["id"] = 0
    out["lesson"] = {"id": 0, "number": 0, "title": lesson.title, "course_id": 0, "course_name": course.name if course else "", "chapter": None}
    out["mode"] = sh.mode
    return out


@router.get("/lab/files/{file_id}")
async def shared_lab_file(token: str, file_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    lesson, _ = await _open(db, token)
    return await owner_labs.get_file(lesson.id, file_id, db)


@router.get("/lab/files/{file_id}/raw")
async def shared_lab_raw(token: str, file_id: int, db: AsyncSession = Depends(get_db)):
    lesson, _ = await _open(db, token)
    return await owner_labs.raw_file(lesson.id, file_id, db)


@router.put("/lab/files/{file_id}/content")
async def shared_lab_content(token: str, file_id: int, body: owner_labs.ContentSave, db: AsyncSession = Depends(get_db)) -> dict:
    lesson, _ = await _open(db, token, write=True)
    return await owner_labs.save_content(lesson.id, file_id, body, db)


@router.put("/lab/notes")
async def shared_lab_notes(token: str, body: owner_labs.NotesSave, db: AsyncSession = Depends(get_db)) -> dict:
    lesson, _ = await _open(db, token, write=True)
    return await owner_labs.save_notes(lesson.id, body, db)


@router.put("/lab/comments/{comment_id}")
async def shared_lab_comment(token: str, comment_id: str, body: owner_labs.CommentSave, db: AsyncSession = Depends(get_db)) -> dict:
    lesson, _ = await _open(db, token, write=True)
    return await owner_labs.save_comment(lesson.id, comment_id, body, db)


@router.delete("/lab/comments/{comment_id}")
async def shared_lab_uncomment(token: str, comment_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    lesson, _ = await _open(db, token, write=True)
    return await owner_labs.delete_comment(lesson.id, comment_id, db)

