"""Laboratories: a lesson's lab, with the files explained in class. The routes hang off the lesson (one lab per lesson), so a
share link of the lesson can reach the same lab. Files are never executed: text is shown as text, every download is an
attachment that the browser may not sniff."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..db import get_db
from ..models import Chapter, Course, Lab, LabComment, LabFile, Lesson
from ..security.auth import require_admin
from ..services import blobs
from ..services import labs as lb

router = APIRouter(dependencies=[Depends(require_admin)])

# Every file goes out as an opaque attachment (the guard adds `nosniff` to every answer): never rendered, never run.
SAFE_HEADERS = {"Content-Security-Policy": "sandbox; default-src 'none'", "Cache-Control": "private, no-cache"}


def _now() -> datetime:
    return datetime.now(UTC)


def file_out(f: LabFile) -> dict[str, Any]:
    return {"id": f.id, "path": f.path, "kind": f.kind, "language": f.language, "size": f.size, "version": f.version, "updated_at": f.updated_at}


def comment_out(c: LabComment) -> dict[str, Any]:
    return {"id": c.id, "file_id": c.file_id, "anchor": c.anchor, "body": c.body, "version": c.version, "created_at": c.created_at, "updated_at": c.updated_at}


async def _comments(db: AsyncSession, lab_id: int) -> list[LabComment]:
    return list((await db.execute(select(LabComment).where(LabComment.lab_id == lab_id).order_by(LabComment.created_at, LabComment.id))).scalars())


async def _files(db: AsyncSession, lab_id: int) -> list[LabFile]:
    return list((await db.execute(select(LabFile).where(LabFile.lab_id == lab_id).order_by(LabFile.path))).scalars())


async def _lesson(db: AsyncSession, lesson_id: int) -> Lesson:
    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return lesson


async def _lab(db: AsyncSession, lesson_id: int) -> Lab:
    lab = (await db.execute(select(Lab).where(Lab.lesson_id == lesson_id))).scalar_one_or_none()
    if lab is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return lab


async def _file(db: AsyncSession, lab: Lab, file_id: int) -> LabFile:
    f = await db.get(LabFile, file_id)
    if f is None or f.lab_id != lab.id:
        raise HTTPException(status_code=404, detail="Not Found")
    return f


async def lab_out(db: AsyncSession, lab: Lab) -> dict[str, Any]:
    lesson = await _lesson(db, lab.lesson_id)
    course = await db.get(Course, lesson.course_id)
    chapter = await db.get(Chapter, lesson.chapter_id) if lesson.chapter_id else None
    return {
        "id": lab.id, "created_at": lab.created_at, "updated_at": lab.updated_at,
        "lesson": {
            "id": lesson.id, "number": lesson.number, "title": lesson.title, "course_id": lesson.course_id,
            "course_name": course.name if course else "", "chapter": {"id": chapter.id, "title": chapter.title} if chapter else None,
        },
        "files": [file_out(f) for f in await _files(db, lab.id)],
        "comments": [comment_out(c) for c in await _comments(db, lab.id)],
    }


async def lab_counts(db: AsyncSession, lesson_ids: list[int]) -> dict[int, dict[str, int]]:
    """lesson id -> {files, comments} for the lessons that have a lab."""
    files = select(func.count(LabFile.id)).where(LabFile.lab_id == Lab.id).scalar_subquery()
    comments = select(func.count(LabComment.id)).where(LabComment.lab_id == Lab.id).scalar_subquery()
    rows = (await db.execute(select(Lab.lesson_id, files, comments).where(Lab.lesson_id.in_(lesson_ids)))).all()
    return {lid: {"files": nf, "comments": nc} for lid, nf, nc in rows}


# --------------------------------------------------------------------------- the lab


@router.get("/lessons/{lesson_id}/lab")
async def get_lab(lesson_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    return await lab_out(db, await _lab(db, lesson_id))


@router.get("/courses/{course_id}/lessons/{number}/lab")
async def get_lab_by_number(course_id: int, number: int, db: AsyncSession = Depends(get_db)) -> dict:
    lesson = (await db.execute(select(Lesson).where(Lesson.course_id == course_id, Lesson.number == number))).scalar_one_or_none()
    if lesson is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return await lab_out(db, await _lab(db, lesson.id))


@router.post("/lessons/{lesson_id}/lab", status_code=201)
async def create_lab(lesson_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """The lesson's lab: made if it has none, else the one it has."""
    lesson = await _lesson(db, lesson_id)
    lab = (await db.execute(select(Lab).where(Lab.lesson_id == lesson.id))).scalar_one_or_none()
    if lab is None:
        lab = Lab(lesson_id=lesson.id)
        db.add(lab)
        await db.commit()
    return await lab_out(db, lab)


@router.delete("/lessons/{lesson_id}/lab")
async def delete_lab(lesson_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    await db.delete(await _lab(db, lesson_id))
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- files


@router.post("/lessons/{lesson_id}/lab/files")
async def upload_file(lesson_id: int, request: Request, path: str = Query(..., max_length=lb.MAX_PATH * 2), db: AsyncSession = Depends(get_db)) -> dict:
    """Body = the file, raw. One call per file (the page sends several in a row). A file with the same name is replaced."""
    lab = await _lab(db, lesson_id)
    try:
        name = lb.clean_path(path)
    except lb.PathError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    existing = (await db.execute(select(LabFile).where(LabFile.lab_id == lab.id, LabFile.path == name))).scalar_one_or_none()
    if existing is None and len(await _files(db, lab.id)) >= lb.MAX_FILES:
        raise HTTPException(status_code=409, detail=f"Il laboratorio ha già {lb.MAX_FILES} file")
    d = config.data_dir / "uploads" / "labs"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"part-{os.urandom(6).hex()}"
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > lb.MAX_FILE_BYTES:
                    raise HTTPException(status_code=413, detail=f"«{name}» supera i {lb.MAX_FILE_BYTES // (1024 * 1024)} MB")
                f.write(chunk)
        data = tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)
    c = lb.classify(name, data)
    blob = None if c.content is not None else blobs.put_bytes(data)
    now = _now()
    if existing is None:
        existing = LabFile(lab_id=lab.id, path=name, kind=c.kind, language=c.language, size=size, content=c.content, blob=blob)
        db.add(existing)
        replaced = False
    else:
        existing.kind, existing.language, existing.size, existing.content, existing.blob = c.kind, c.language, size, c.content, blob
        existing.version += 1
        existing.updated_at = now
        replaced = True
    lab.updated_at = now
    await db.commit()
    return {**file_out(existing), "replaced": replaced}


@router.get("/lessons/{lesson_id}/lab/files/{file_id}")
async def get_file(lesson_id: int, file_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """A file with its text (text and notebooks); the others are read through `/raw`."""
    f = await _file(db, await _lab(db, lesson_id), file_id)
    return {**file_out(f), "content": f.content}


class FilePatch(BaseModel):
    path: str = Field(min_length=1, max_length=lb.MAX_PATH * 2)


@router.patch("/lessons/{lesson_id}/lab/files/{file_id}")
async def rename_file(lesson_id: int, file_id: int, body: FilePatch, db: AsyncSession = Depends(get_db)) -> dict:
    lab = await _lab(db, lesson_id)
    f = await _file(db, lab, file_id)
    try:
        name = lb.clean_path(body.path)
    except lb.PathError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if name != f.path:
        if (await db.execute(select(LabFile.id).where(LabFile.lab_id == lab.id, LabFile.path == name))).scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"C'è già un file «{name}»")
        f.path = name
        if f.kind == "text":
            f.language = lb.language_for(name) or "text"
        f.updated_at = lab.updated_at = _now()
        await db.commit()
    return file_out(f)


@router.delete("/lessons/{lesson_id}/lab/files/{file_id}")
async def delete_file(lesson_id: int, file_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    lab = await _lab(db, lesson_id)
    await db.delete(await _file(db, lab, file_id))
    lab.updated_at = _now()
    await db.commit()
    return {"ok": True}


@router.get("/lessons/{lesson_id}/lab/files/{file_id}/raw")
async def raw_file(lesson_id: int, file_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    """The file to download (text in UTF-8). Always an attachment; only pictures and PDFs recognised by their bytes keep their
    type, so the viewer can draw them."""
    f = await _file(db, await _lab(db, lesson_id), file_id)
    filename = f.path.rsplit("/", 1)[-1]
    disposition = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    if f.content is not None:
        return Response(f.content.encode(), media_type="application/octet-stream", headers={**SAFE_HEADERS, "Content-Disposition": disposition})
    if not f.blob or not blobs.exists(f.blob):
        raise HTTPException(status_code=404, detail="Not Found")
    path = blobs.path_for(f.blob)
    with open(path, "rb") as fh:
        head = fh.read(16)
    return FileResponse(path, media_type=lb.media_type(f.kind, head), headers={**SAFE_HEADERS, "Content-Disposition": disposition})



# --------------------------------------------------------------------------- comments


class CommentSave(BaseModel):
    file_id: int
    anchor: dict[str, Any] = Field(default_factory=dict)
    body: str = Field("", max_length=lb.MAX_COMMENT_CHARS)


@router.put("/lessons/{lesson_id}/lab/comments/{comment_id}")
async def save_comment(lesson_id: int, comment_id: str, body: CommentSave, db: AsyncSession = Depends(get_db)) -> dict:
    """Create or replace a comment (the id comes from the page: sending the same comment twice never doubles it). A comment
    stays on the file it was made on."""
    if not lb.COMMENT_ID.match(comment_id):
        raise HTTPException(status_code=422, detail="Id del commento non valido")
    lab = await _lab(db, lesson_id)
    try:
        anchor = lb.clean_anchor(body.anchor)
    except lb.AnchorError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    c = await db.get(LabComment, comment_id)
    if c is not None and (c.lab_id != lab.id or c.file_id != body.file_id):
        raise HTTPException(status_code=404, detail="Not Found")
    if c is None:
        await _file(db, lab, body.file_id)
        if (await db.execute(select(func.count(LabComment.id)).where(LabComment.lab_id == lab.id))).scalar_one() >= lb.MAX_COMMENTS:
            raise HTTPException(status_code=409, detail="Troppi commenti in questo laboratorio")
        c = LabComment(id=comment_id, lab_id=lab.id, file_id=body.file_id, anchor=anchor, body=body.body.replace("\x00", ""))
        db.add(c)
    else:
        c.anchor, c.body = anchor, body.body.replace("\x00", "")
        c.version += 1
        c.updated_at = _now()
    lab.updated_at = _now()
    await db.commit()
    return comment_out(c)


@router.delete("/lessons/{lesson_id}/lab/comments/{comment_id}")
async def delete_comment(lesson_id: int, comment_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Remove a comment; one that is already gone is fine (a retried delete)."""
    lab = await _lab(db, lesson_id)
    c = await db.get(LabComment, comment_id) if lb.COMMENT_ID.match(comment_id) else None
    if c is not None and c.lab_id == lab.id:
        await db.delete(c)
        lab.updated_at = _now()
        await db.commit()
    return {"ok": True}
