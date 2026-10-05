"""Lessons: the live-notes workspace. Slides (PDF) with typed Markdown notes and pen strokes per page,
saved as they are written; then "generate" hands the lesson to the import, which writes the course text
from the notes and the annotated slides."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..db import get_db
from ..models import Chapter, Course, Job, Lesson, LessonPage, LessonShare, Upload
from ..security.auth import require_admin
from ..services import blobs
from ..services import jobs as jobs_svc
from ..services import lessons as ls
from ..services import settings as settings_svc
from ..services.texttools import slugify
from . import labs
from .courses import GUIDELINES_MAX

router = APIRouter(dependencies=[Depends(require_admin)])

NOTEBOOK_RATIO = 1.4142  # a blank page of a lesson without slides: A4, portrait
DEFAULT_SLIDE_RATIO = 0.5625


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- output


def page_out(p: LessonPage) -> dict[str, Any]:
    return {
        "id": p.id, "position": p.position, "kind": p.kind, "slide_page": p.slide_page, "ratio": p.ratio,
        "notes": p.notes, "ink": p.ink, "version": p.version,
    }


def lesson_out(lesson: Lesson, course_name: str | None = None, stats: dict[str, int] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": lesson.id, "course_id": lesson.course_id, "number": lesson.number, "title": lesson.title, "status": lesson.status, "has_pdf": bool(lesson.pdf_blob),
        "pdf_name": lesson.pdf_name, "pdf_pages": lesson.pdf_pages, "generated_at": lesson.generated_at,
        "last_result": lesson.last_result, "chapter_id": lesson.chapter_id, "last_page_id": lesson.last_page_id, "created_at": lesson.created_at, "updated_at": lesson.updated_at,
    }
    if course_name is not None:
        out["course_name"] = course_name
    if stats is not None:
        out.update(stats)
    return out


async def _lesson(db: AsyncSession, lesson_id: int) -> Lesson:
    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return lesson


async def _next_number(db: AsyncSession, course_id: int) -> int:
    return ((await db.execute(select(func.max(Lesson.number)).where(Lesson.course_id == course_id))).scalar_one_or_none() or 0) + 1


async def _pages(db: AsyncSession, lesson_id: int) -> list[LessonPage]:
    return list((await db.execute(select(LessonPage).where(LessonPage.lesson_id == lesson_id).order_by(LessonPage.position, LessonPage.id))).scalars())


async def _course(db: AsyncSession, course_id: int) -> Course:
    c = await db.get(Course, course_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return c


# --------------------------------------------------------------------------- lessons


class LessonIn(BaseModel):
    course_id: int
    title: str = Field(min_length=1, max_length=300)


class LessonPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    course_id: int | None = None
    status: Literal["working", "completed"] | None = None


@router.get("/lessons")
async def list_lessons(course_id: int | None = None, db: AsyncSession = Depends(get_db)) -> list[dict]:
    q = select(Lesson, Course.name).join(Course, Course.id == Lesson.course_id).order_by(Lesson.updated_at.desc(), Lesson.id.desc())
    if course_id is not None:
        q = q.where(Lesson.course_id == course_id)
    rows = (await db.execute(q.limit(500))).all()
    stats = {
        lid: {"page_count": n, "notes_pages": notes or 0, "ink_pages": ink or 0}
        for lid, n, notes, ink in (
            await db.execute(
                select(
                    LessonPage.lesson_id,
                    func.count(),
                    func.count().filter(func.length(func.trim(LessonPage.notes)) > 0),
                    func.count().filter(func.jsonb_array_length(LessonPage.ink) > 0),
                ).group_by(LessonPage.lesson_id)
            )
        ).all()
    }
    labs_of = await labs.lab_counts(db, [l.id for l, _ in rows])
    return [
        {**lesson_out(l, name, stats.get(l.id, {"page_count": 0, "notes_pages": 0, "ink_pages": 0})), "lab": labs_of.get(l.id)}
        for l, name in rows
    ]


@router.post("/lessons", status_code=201)
async def create_lesson(body: LessonIn, db: AsyncSession = Depends(get_db)) -> dict:
    course = await _course(db, body.course_id)
    lesson = Lesson(course_id=course.id, number=await _next_number(db, course.id), title=body.title.strip())
    db.add(lesson)
    await db.flush()
    # A lesson starts with one blank page; attaching slides replaces it while it is still untouched.
    db.add(LessonPage(lesson_id=lesson.id, position=1, kind="blank", ratio=NOTEBOOK_RATIO))
    await db.commit()
    return lesson_out(lesson, course.name)


@router.get("/lessons/{lesson_id}")
async def get_lesson(lesson_id: int, pages: bool = True, db: AsyncSession = Depends(get_db)) -> dict:
    lesson = await _lesson(db, lesson_id)
    course = await db.get(Course, lesson.course_id)
    out = lesson_out(lesson, course.name if course else None)
    out["course_guidelines"] = (course.guidelines or "") if course else ""
    chapter = await db.get(Chapter, lesson.chapter_id) if lesson.chapter_id else None
    out["chapter"] = {"id": chapter.id, "title": chapter.title, "position": chapter.position} if chapter else None
    out["lab"] = (await labs.lab_counts(db, [lesson.id])).get(lesson.id)
    if pages:
        out["pages"] = [page_out(p) for p in await _pages(db, lesson.id)]
    return out


@router.get("/courses/{course_id}/lessons/{number}")
async def get_lesson_by_number(course_id: int, number: int, pages: bool = True, db: AsyncSession = Depends(get_db)) -> dict:
    """A lesson by its address in the URL: the course and the lesson's number within it."""
    lesson = (await db.execute(select(Lesson).where(Lesson.course_id == course_id, Lesson.number == number))).scalar_one_or_none()
    if lesson is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return await get_lesson(lesson.id, pages, db)


@router.patch("/lessons/{lesson_id}")
async def patch_lesson(lesson_id: int, body: LessonPatch, db: AsyncSession = Depends(get_db)) -> dict:
    lesson = await _lesson(db, lesson_id)
    if body.title is not None:
        lesson.title = body.title.strip()
    if body.status is not None:
        lesson.status = body.status
    if body.course_id is not None and body.course_id != lesson.course_id:
        target = await _course(db, body.course_id)
        lesson.number = await _next_number(db, target.id)  # before the move: the query flushes the lesson
        lesson.course_id = target.id
        lesson.chapter_id = None  # its text stays where it was written; the new course has no chapter of this lesson
    lesson.updated_at = _now()
    await db.commit()
    return lesson_out(lesson)


@router.delete("/lessons/{lesson_id}")
async def delete_lesson(lesson_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    lesson = await _lesson(db, lesson_id)
    await db.delete(lesson)
    await db.commit()
    return {"ok": True}


@router.post("/lessons/{lesson_id}/slides")
async def attach_slides(lesson_id: int, request: Request, name: str = Query("slide.pdf", max_length=500), db: AsyncSession = Depends(get_db)) -> dict:
    """Body = the slides as a raw PDF. Only for a lesson without slides; its untouched blank pages are replaced."""
    lesson = await _lesson(db, lesson_id)
    if lesson.pdf_blob:
        raise HTTPException(status_code=409, detail="La lezione ha già le slide")
    limits = await settings_svc.get_section(db, "uploads")
    max_bytes = limits.max_file_mb * 1024 * 1024
    d = config.data_dir / "uploads" / "lessons"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"part-{os.urandom(6).hex()}"
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"file larger than {limits.max_file_mb} MB")
                f.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="file vuoto")
        with open(tmp, "rb") as f:
            if f.read(5) != b"%PDF-":
                raise HTTPException(status_code=400, detail="Il file non è un PDF")
        try:
            ratios = await asyncio.to_thread(ls.pdf_page_ratios, tmp)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        blob = blobs.put_file(tmp, move=True)
    finally:
        tmp.unlink(missing_ok=True)

    existing = await _pages(db, lesson.id)
    untouched = [p for p in existing if p.kind == "blank" and not p.notes.strip() and not p.ink]
    for p in untouched:
        await db.delete(p)
    kept = [p for p in existing if p not in untouched]
    for i, p in enumerate(kept):
        p.position = len(ratios) + 1 + i
    for n, ratio in enumerate(ratios, start=1):
        db.add(LessonPage(lesson_id=lesson.id, position=n, kind="slide", slide_page=n, ratio=ratio))
    lesson.pdf_blob, lesson.pdf_name, lesson.pdf_pages = blob, re.sub(r"[\x00-\x1f/\\]", "", name)[:200] or "slide.pdf", len(ratios)
    lesson.updated_at = _now()
    await db.commit()
    return await get_lesson(lesson.id, True, db)


@router.get("/lessons/{lesson_id}/pdf")
async def slides_pdf(lesson_id: int, db: AsyncSession = Depends(get_db)) -> FileResponse:
    lesson = await _lesson(db, lesson_id)
    if not lesson.pdf_blob or not blobs.exists(lesson.pdf_blob):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(
        blobs.path_for(lesson.pdf_blob), media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="slide.pdf"', "Cache-Control": "private, max-age=3600",
                 "Content-Security-Policy": "sandbox; default-src 'none'"},
    )


# --------------------------------------------------------------------------- pages


class PageSave(BaseModel):
    notes: str | None = Field(None, max_length=ls.MAX_NOTES_CHARS)
    ink: list[dict[str, Any]] | None = None


class PageAdd(BaseModel):
    after_page_id: int | None = None  # None = at the end


async def _page(db: AsyncSession, lesson_id: int, page_id: int) -> LessonPage:
    p = await db.get(LessonPage, page_id)
    if p is None or p.lesson_id != lesson_id:
        raise HTTPException(status_code=404, detail="Not Found")
    return p


@router.put("/lessons/{lesson_id}/pages/{page_id}")
async def save_page(lesson_id: int, page_id: int, body: PageSave, db: AsyncSession = Depends(get_db)) -> dict:
    """Save the notes and/or the strokes of one page (what is sent replaces what is stored)."""
    p = await _page(db, lesson_id, page_id)
    if body.notes is not None:
        p.notes = body.notes.replace("\x00", "")
    if body.ink is not None:
        try:
            p.ink = ls.clean_ink(body.ink)
        except ls.InkError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
    p.version += 1
    p.updated_at = _now()
    await db.execute(update(Lesson).where(Lesson.id == lesson_id).values(updated_at=p.updated_at))
    await db.commit()
    return {"version": p.version, "updated_at": p.updated_at}


class Bookmark(BaseModel):
    page_id: int


@router.put("/lessons/{lesson_id}/last-page")
async def set_last_page(lesson_id: int, body: Bookmark, db: AsyncSession = Depends(get_db)) -> dict:
    """Remember the page being looked at (opening the lesson again goes there). Doesn't count as a change of the lesson."""
    await _page(db, lesson_id, body.page_id)
    await db.execute(update(Lesson).where(Lesson.id == lesson_id).values(last_page_id=body.page_id))
    await db.commit()
    return {"ok": True}


@router.post("/lessons/{lesson_id}/pages", status_code=201)
async def add_page(lesson_id: int, body: PageAdd, db: AsyncSession = Depends(get_db)) -> dict:
    """A blank page after the given one (a place to write more than the slide has room for)."""
    lesson = await _lesson(db, lesson_id)
    pages = await _pages(db, lesson.id)
    if len(pages) >= ls.MAX_PAGES:
        raise HTTPException(status_code=409, detail="Troppe pagine")
    after = 0
    if body.after_page_id is not None:
        after = (await _page(db, lesson.id, body.after_page_id)).position
    else:
        after = pages[-1].position if pages else 0
    slides = [p.ratio for p in pages if p.kind == "slide"]
    ratio = slides[0] if slides else NOTEBOOK_RATIO
    await db.execute(update(LessonPage).where(LessonPage.lesson_id == lesson.id, LessonPage.position > after).values(position=LessonPage.position + 1))
    page = LessonPage(lesson_id=lesson.id, position=after + 1, kind="blank", ratio=ratio)
    db.add(page)
    lesson.updated_at = _now()
    await db.commit()
    return page_out(page)


@router.delete("/lessons/{lesson_id}/pages/{page_id}")
async def delete_page(lesson_id: int, page_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Remove a page: one added by hand or a slide (the slides PDF itself is kept, the page just leaves the lesson)."""
    p = await _page(db, lesson_id, page_id)
    if len(await _pages(db, lesson_id)) <= 1:
        raise HTTPException(status_code=409, detail="Serve almeno una pagina")
    pos = p.position
    await db.delete(p)
    await db.flush()
    await db.execute(update(LessonPage).where(LessonPage.lesson_id == lesson_id, LessonPage.position > pos).values(position=LessonPage.position - 1))
    await db.execute(update(Lesson).where(Lesson.id == lesson_id).values(updated_at=_now()))
    await db.commit()
    return {"ok": True}


class PageRestore(BaseModel):
    id: int  # the page's own id: the editor's undo history still points at it
    kind: Literal["slide", "blank"]
    slide_page: int | None = None
    ratio: float = Field(gt=0.1, lt=10)
    position: int = Field(ge=1)
    notes: str = Field("", max_length=ls.MAX_NOTES_CHARS)
    ink: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/lessons/{lesson_id}/pages/restore", status_code=201)
async def restore_page(lesson_id: int, body: PageRestore, db: AsyncSession = Depends(get_db)) -> dict:
    """Undo of a page removal: the page comes back with its own id, notes and strokes, at its place in the lesson."""
    lesson = await _lesson(db, lesson_id)
    pages = await _pages(db, lesson.id)
    if len(pages) >= ls.MAX_PAGES:
        raise HTTPException(status_code=409, detail="Troppe pagine")
    # Only an id that was handed out before can come back (the sequence is past it, so no new page can ever take it).
    issued = (await db.execute(text("SELECT pg_sequence_last_value(pg_get_serial_sequence('lesson_pages', 'id')::regclass)"))).scalar_one_or_none() or 0
    if body.id > issued:
        raise HTTPException(status_code=422, detail="Pagina sconosciuta")
    if await db.get(LessonPage, body.id) is not None:
        raise HTTPException(status_code=409, detail="La pagina c'è già")
    if body.kind == "slide":
        if not lesson.pdf_blob or not body.slide_page or not 1 <= body.slide_page <= lesson.pdf_pages:
            raise HTTPException(status_code=422, detail="Slide non valida")
        if any(p.slide_page == body.slide_page for p in pages):
            raise HTTPException(status_code=409, detail="La slide c'è già")
    try:
        ink = ls.clean_ink(body.ink)
    except ls.InkError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    pos = min(body.position, len(pages) + 1)
    await db.execute(update(LessonPage).where(LessonPage.lesson_id == lesson.id, LessonPage.position >= pos).values(position=LessonPage.position + 1))
    page = LessonPage(
        id=body.id, lesson_id=lesson.id, position=pos, kind=body.kind, slide_page=body.slide_page if body.kind == "slide" else None,
        ratio=body.ratio, notes=body.notes.replace("\x00", ""), ink=ink,
    )
    db.add(page)
    lesson.updated_at = _now()
    await db.commit()
    return page_out(page)


class SyncIn(BaseModel):
    known: dict[int, int] = Field(default_factory=dict)  # page id -> the version the caller has


async def sync_out(db: AsyncSession, lesson: Lesson, known: dict[int, int]) -> dict[str, Any]:
    """What changed since the caller last looked: the order of the pages and the pages it doesn't have (or has another version of).
    Used by the shared views and by an editor that has share links, to follow what others write."""
    pages = await _pages(db, lesson.id)
    return {"title": lesson.title, "order": [p.id for p in pages], "pages": [page_out(p) for p in pages if known.get(p.id) != p.version]}


@router.post("/lessons/{lesson_id}/sync")
async def sync_lesson(lesson_id: int, body: SyncIn, db: AsyncSession = Depends(get_db)) -> dict:
    return await sync_out(db, await _lesson(db, lesson_id), body.known)


# --------------------------------------------------------------------------- share links


class ShareIn(BaseModel):
    mode: Literal["read", "write"]
    new_link: bool = False  # replace the link of this mode with a new one (the old one stops working)


def share_out(sh: LessonShare) -> dict[str, Any]:
    return {"mode": sh.mode, "token": sh.token, "created_at": sh.created_at, "last_used_at": sh.last_used_at}


async def _shares(db: AsyncSession, lesson_id: int) -> list[LessonShare]:
    return list((await db.execute(select(LessonShare).where(LessonShare.lesson_id == lesson_id).order_by(LessonShare.mode))).scalars())


@router.get("/lessons/{lesson_id}/shares")
async def list_shares(lesson_id: int, db: AsyncSession = Depends(get_db)) -> list[dict]:
    await _lesson(db, lesson_id)
    return [share_out(sh) for sh in await _shares(db, lesson_id)]


@router.post("/lessons/{lesson_id}/shares", status_code=201)
async def create_share(lesson_id: int, body: ShareIn, db: AsyncSession = Depends(get_db)) -> dict:
    """The link for this mode: made if there is none (or when asked for a new one), else the one that exists."""
    await _lesson(db, lesson_id)
    existing = next((sh for sh in await _shares(db, lesson_id) if sh.mode == body.mode), None)
    if existing is not None and not body.new_link:
        return share_out(existing)
    if existing is not None:
        await db.delete(existing)
        await db.flush()
    sh = LessonShare(lesson_id=lesson_id, mode=body.mode, token=secrets.token_urlsafe(24))
    db.add(sh)
    await db.commit()
    return share_out(sh)


@router.delete("/lessons/{lesson_id}/shares/{mode}")
async def delete_share(lesson_id: int, mode: Literal["read", "write"], db: AsyncSession = Depends(get_db)) -> dict:
    await _lesson(db, lesson_id)
    await db.execute(LessonShare.__table__.delete().where(LessonShare.lesson_id == lesson_id, LessonShare.mode == mode))
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- downloads


def _file_stem(title: str) -> str:
    return slugify(title, 60)


@router.get("/lessons/{lesson_id}/annotated.pdf")
async def annotated_pdf(lesson_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    """The lesson as a PDF: the slides (and the pages added by hand) with the strokes on them."""
    lesson = await _lesson(db, lesson_id)
    pages = [page_out(p) for p in await _pages(db, lesson.id)]
    path = blobs.path_for(lesson.pdf_blob) if lesson.pdf_blob and blobs.exists(lesson.pdf_blob) else None
    data = await asyncio.to_thread(ls.annotated_pdf, path, pages)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{_file_stem(lesson.title)}.pdf"'})


@router.get("/lessons/{lesson_id}/notes.md")
async def notes_md(lesson_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    lesson = await _lesson(db, lesson_id)
    pages = [page_out(p) for p in await _pages(db, lesson.id)]
    md = ls.notes_markdown(lesson.title, pages) or f"# {lesson.title}\n"
    return Response(md, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{_file_stem(lesson.title)}.md"'})


# --------------------------------------------------------------------------- generate the text


class GenerateIn(BaseModel):
    # What the student wants from the text of this subject. None = leave it as saved for the course;
    # "" = none (the AI decides).
    guidelines: str | None = Field(None, max_length=GUIDELINES_MAX)
    save_guidelines: bool = True
    chapter_id: int | None = None  # continue this chapter instead of the default
    # Without a chapter: "update" (the chapter this lesson already lives in, its section rewritten to match the lesson now),
    # "new_chapter" (one lesson, one chapter) or "auto" (Lecta compares with the existing chapters).
    # Left out: "update" when the lesson has a chapter, else "new_chapter".
    placement: Literal["update", "new_chapter", "auto"] | None = None
    # Set the lesson «Completata» once its text is written into a chapter (the dialog's tick, on by default).
    mark_completed: bool = True


@router.post("/lessons/{lesson_id}/generate")
async def generate(lesson_id: int, body: GenerateIn, db: AsyncSession = Depends(get_db)) -> dict:
    lesson = await _lesson(db, lesson_id)
    course = await _course(db, lesson.course_id)
    pages = await _pages(db, lesson.id)
    if not lesson.pdf_blob and not any(p.notes.strip() or p.ink for p in pages):
        raise HTTPException(status_code=400, detail="La lezione è vuota: scrivi qualche appunto o carica le slide")
    chapter_id, placement = body.chapter_id, body.placement
    if chapter_id is not None:
        ch = await db.get(Chapter, chapter_id)
        if ch is None or ch.course_id != course.id:
            raise HTTPException(status_code=404, detail="Not Found")
    elif placement in (None, "update"):
        # The lesson's text already lives in a chapter: the new version goes there, integrated with what is in it.
        own = await db.get(Chapter, lesson.chapter_id) if lesson.chapter_id else None
        if own is not None and own.course_id == course.id:
            chapter_id = own.id
        elif placement == "update":
            raise HTTPException(status_code=409, detail="Il testo di questa lezione non è ancora in nessun capitolo")
        placement = "new_chapter" if chapter_id is None else placement
    running = (
        await db.execute(select(Job.id).where(Job.kind == "ingest", Job.status.in_(jobs_svc.ACTIVE), Job.payload["lesson_id"].astext == str(lesson.id)).limit(1))
    ).scalar_one_or_none()
    if running is not None:
        raise HTTPException(status_code=409, detail="Il testo di questa lezione si sta già generando")

    if body.guidelines is not None:
        text = body.guidelines.strip()
        if body.save_guidelines:
            course.guidelines = text or None
    else:
        text = (course.guidelines or "").strip()

    snapshot = {
        "lesson_id": lesson.id, "title": lesson.title, "pdf_blob": lesson.pdf_blob, "pdf_name": lesson.pdf_name, "pdf_pages": lesson.pdf_pages,
        "pages": [{"position": p.position, "kind": p.kind, "slide_page": p.slide_page, "ratio": p.ratio, "notes": p.notes, "ink": p.ink} for p in pages],
    }
    snap_blob = blobs.put_bytes(json.dumps(snapshot, ensure_ascii=False).encode())
    up = Upload(status="queued", target_course_id=course.id, target_chapter_id=chapter_id, note=f"Lezione: {lesson.title}"[:2000], via="lesson")
    db.add(up)
    await db.flush()
    (config.data_dir / "uploads" / str(up.id)).mkdir(parents=True, exist_ok=True)
    job = await jobs_svc.enqueue(
        db, "ingest", {"upload_id": up.id, "lesson_id": lesson.id, "lesson_snapshot": snap_blob, "guidelines": text,
         "mark_completed": body.mark_completed, **({"placement": placement} if chapter_id is None else {})},
        title=f"Lezione: {lesson.title}"[:200], priority="ingest", course_id=course.id, commit=False,
    )
    up.job_id = job.id
    await db.commit()
    return {"job_id": job.id, "upload_id": up.id}

