"""Draft preview (HTML) and downloads of a course's LaTeX project (admin)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Chapter, Course
from ..security.auth import require_admin
from ..services import preview, projects
from ..services.source_export import build_source_zip, project_files, zip_name
from .courses import chapter_out, load_chapter, load_course

router = APIRouter(dependencies=[Depends(require_admin)])


async def _render(db: AsyncSession, course: Course, ch: Chapter, files: set[str]) -> dict[str, Any]:
    pf = await projects.get_file(db, course.id, ch.path)
    source = (pf.text_content if pf is not None else "") or ""
    info = {"id": ch.id, "title": ch.title, "path": ch.path, "position": ch.position}
    try:
        return await preview.render_chapter(
            course_id=course.id, language=course.language, chapter=info, source=source, blob=pf.blob if pf else "-", files=files,
            preamble=await projects.preamble_for(db, course),
        )
    except preview.PreviewError as e:
        return {"chapter": info, "html": "", "toc": [], "warnings": [str(e)], "blob": pf.blob if pf else None, "took_ms": 0, "error": str(e)}


@router.get("/courses/{course_id}/chapters/{chapter_id}/preview")
async def chapter_preview(course_id: int, chapter_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    ch = await load_chapter(db, c, chapter_id)
    return await _render(db, c, ch, set((await projects.manifest(db, c.id)).keys()))


@router.get("/courses/{course_id}/preview")
async def course_preview(course_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    files = set((await projects.manifest(db, c.id)).keys())
    return {"chapters": [await _render(db, c, ch, files) for ch in await projects.chapters_of(db, c.id)]}


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
