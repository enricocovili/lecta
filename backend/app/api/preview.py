"""Draft preview (HTML, and the chapters typeset block by block as SVG) and downloads of a course's LaTeX project (admin)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Chapter, Course
from ..security.auth import require_admin
from ..services import draft, latex, preview, projects
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
