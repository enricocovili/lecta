"""The draft (chapters typeset by LaTeX block by block, as SVG) and downloads of a course's LaTeX project (admin)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Chapter, Course
from ..security.auth import require_admin
from ..services import draft, latex, projects
from ..services.source_export import build_source_zip, project_files, zip_name
from .courses import chapter_out, load_chapter, load_course

router = APIRouter(dependencies=[Depends(require_admin)])


# --------------------------------------------------------------------------- draft typeset by LaTeX


async def _draft(db: AsyncSession, course: Course, ch: Chapter, inputs: draft.Inputs) -> dict[str, Any]:
    try:
        return await draft.render_chapter(db, course, ch, inputs)
    except latex.CompileServiceError as e:
        info = {"id": ch.id, "title": ch.title, "path": ch.path, "position": ch.position}
        return {"chapter": info, "width": 451.0, "blocks": [], "toc": [], "warnings": [], "typeset": 0, "took_ms": 0,
                "error": f"Composizione LaTeX non disponibile: {e}"}


@router.get("/courses/{course_id}/chapters/{chapter_id}/draft")
async def chapter_draft(course_id: int, chapter_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    ch = await load_chapter(db, c, chapter_id)
    return await _draft(db, c, ch, await draft.inputs_for(db, c))


@router.get("/courses/{course_id}/draft")
async def course_draft(course_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    inputs = await draft.inputs_for(db, c)
    return {"chapters": [await _draft(db, c, ch, inputs) for ch in await projects.chapters_of(db, c.id)]}


class BlockIn(BaseModel):
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    src: str = Field(max_length=1024 * 1024)  # the block as it was shown: the edit is refused if it changed since
    text: str = Field(max_length=1024 * 1024)


@router.put("/courses/{course_id}/chapters/{chapter_id}/block")
async def write_block(course_id: int, chapter_id: int, body: BlockIn, db: AsyncSession = Depends(get_db)) -> dict:
    """Replace the lines of one block of the draft with the text written in place (nothing: the block goes away)."""
    c = await load_course(db, course_id)
    ch = await load_chapter(db, c, chapter_id)
    # Serialise with the assistant and the imports writing into the same course (they lock the course row too).
    await db.execute(select(Course.id).where(Course.id == c.id).with_for_update())
    pf = await projects.get_file(db, c.id, ch.path)
    lines = ((pf.text_content if pf is not None else None) or "").split("\n")
    if body.end < body.start or body.end > len(lines) or "\n".join(lines[body.start - 1 : body.end]) != body.src:
        raise HTTPException(status_code=409, detail="Il testo è cambiato da quando l'hai aperto: ricarica e riprova")
    text = body.text.strip("\n")
    before, after = lines[: body.start - 1], lines[body.end :]
    if not text.strip() and before and not before[-1].strip() and after and not after[0].strip():
        after = after[1:]  # the block goes away with one of the blank lines around it
    new = before + (text.split("\n") if text.strip() else []) + after
    if new != lines:
        await projects.write_file(db, c, ch.path, "\n".join(new).encode(), validated=True)
    await db.commit()
    return await _draft(db, c, ch, await draft.inputs_for(db, c))


@router.get("/courses/{course_id}/draft/svg/{name}")
async def draft_svg(course_id: int, name: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    await load_course(db, course_id)
    path = draft.svg_path(course_id, name)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    # The name is the content's address: it never changes. The picture is shown with <img>; opened directly, nothing runs.
    return FileResponse(path, media_type="image/svg+xml", headers={
        "Cache-Control": "private, max-age=31536000, immutable",
        "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'",
        "X-Content-Type-Options": "nosniff",
    })


# --------------------------------------------------------------------------- source download

@router.get("/courses/{course_id}/source.zip")
async def source_zip(course_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    """The LaTeX project of the working version."""
    c = await load_course(db, course_id)
    if not await projects.chapters_of(db, c.id):
        raise HTTPException(status_code=404, detail="Not Found")
    data = build_source_zip(await project_files(db, c), name=c.name)
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{zip_name(c)}"'})


_ = chapter_out
