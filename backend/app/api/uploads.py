"""What an import read: source files (originals, pages), the items and figures it made, and its manifest.

Material comes in from the lessons only (api/lessons.py → pipeline/lesson.py); there is no upload of loose files.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Chapter, Course, IngestFigure, IngestItem, Job, SourceFile, SourceLink, Upload
from ..security.auth import require_admin
from ..services import blobs

router = APIRouter(dependencies=[Depends(require_admin)])

# --------------------------------------------------------------------------- sources


def source_out(s: SourceFile) -> dict[str, Any]:
    return {
        "id": s.id, "upload_id": s.upload_id, "parent_id": s.parent_id, "name": s.name, "folder": s.folder, "kind": s.kind,
        "mime": s.mime, "size": s.size, "pages": s.pages, "language": s.language, "status": s.status, "reason": s.reason,
        "created_at": s.created_at, "has_original": bool(s.blob),
    }


async def _source(db: AsyncSession, sid: int) -> SourceFile:
    s = await db.get(SourceFile, sid)
    if s is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return s


@router.get("/sources/{sid}")
async def get_source(sid: int, db: AsyncSession = Depends(get_db)) -> dict:
    s = await _source(db, sid)
    out = source_out(s)
    items = (await db.execute(select(IngestItem).where(IngestItem.source_file_id == sid).order_by(IngestItem.id))).scalars().all()
    # The latest ingestion's items describe the pages.
    latest_job = max((i.job_id for i in items), default=None)
    out["items"] = [item_out(i) for i in items if i.job_id == latest_job]
    links = (
        await db.execute(
            select(SourceLink, Course.name, Chapter.title)
            .join(Course, Course.id == SourceLink.course_id)
            .outerjoin(Chapter, Chapter.id == SourceLink.chapter_id)
            .where(SourceLink.source_file_id == sid)
        )
    ).all()
    out["links"] = [
        {"course_id": link.course_id, "course_name": cn, "chapter_id": link.chapter_id, "chapter_title": ct, "job_id": link.job_id}
        for link, cn, ct in links
    ]
    return out


@router.get("/sources/{sid}/raw")
async def source_raw(sid: int, download: bool = False, db: AsyncSession = Depends(get_db)) -> FileResponse:
    s = await _source(db, sid)
    if not s.blob or not blobs.exists(s.blob):
        raise HTTPException(status_code=404, detail="Not Found")
    media = s.mime or "application/octet-stream"
    if media.startswith("text/"):
        media += "; charset=utf-8"
    safe_name = re.sub(r'[^\w.\- ]', "_", s.name)[:150] or "file"
    disp = "attachment" if download or media not in ("application/pdf", "image/png", "image/jpeg", "image/webp") and not media.startswith("text/") else "inline"
    return FileResponse(
        blobs.path_for(s.blob), media_type=media,
        headers={"Content-Disposition": f'{disp}; filename="{safe_name}"', "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"},
    )


@router.get("/blobs/items/{item_id}/{which}")
async def item_image(item_id: int, which: str, db: AsyncSession = Depends(get_db)) -> Response:
    it = await db.get(IngestItem, item_id)
    if it is None or which not in ("preview", "image"):
        raise HTTPException(status_code=404, detail="Not Found")
    h = it.preview_blob if which == "preview" else it.image_blob
    if not h or not blobs.exists(h):
        raise HTTPException(status_code=404, detail="Not Found")
    data = blobs.read_bytes(h)
    return Response(data, media_type="image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png",
                    headers={"Cache-Control": "private, max-age=86400"})


@router.get("/blobs/figures/{fig_id}/{which}")
async def figure_image(fig_id: int, which: str, db: AsyncSession = Depends(get_db)) -> Response:
    f = await db.get(IngestFigure, fig_id)
    if f is None or which != "crop":
        raise HTTPException(status_code=404, detail="Not Found")
    h = f.crop_blob
    if not h or not blobs.exists(h):
        raise HTTPException(status_code=404, detail="Not Found")
    data = blobs.read_bytes(h)
    return Response(data, media_type="image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


def item_out(i: IngestItem) -> dict[str, Any]:
    return {
        "id": i.id, "key": i.key, "job_id": i.job_id, "source_file_id": i.source_file_id, "page": i.page,
        "position": i.position, "kind": i.kind, "kind_source": i.kind_source, "label": i.label, "title": i.title,
        "text": (i.text or "")[:4000], "has_latex": bool(i.latex), "language": i.language,
        "group_key": i.group_key, "width": i.width, "height": i.height,
        "preview_url": f"/api/blobs/items/{i.id}/preview" if i.preview_blob else None,
        "image_url": f"/api/blobs/items/{i.id}/image" if i.image_blob else None,
        "meta": {k: v for k, v in (i.meta or {}).items() if k in ("route", "why", "pictures", "skipped_images", "code_blocks", "ops", "math")},
    }


def figure_out(f: IngestFigure) -> dict[str, Any]:
    return {
        "id": f.id, "key": f.key, "item_id": f.item_id, "origin": f.origin, "source_ref": f.source_ref, "bbox": f.bbox,
        "description": f.description, "status": f.status, "path": f.path, "group_key": f.group_key,
        "crop_url": f"/api/blobs/figures/{f.id}/crop" if f.crop_blob else None,
    }


@router.get("/jobs/{job_id}/ingest")
async def ingest_manifest(job_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    up = (await db.execute(select(Upload).where(Upload.job_id == job_id))).scalar_one_or_none()
    files = []
    if up:
        files = [source_out(s) for s in (await db.execute(select(SourceFile).where(SourceFile.upload_id == up.id).order_by(SourceFile.id))).scalars()]
    items = [item_out(i) for i in (await db.execute(select(IngestItem).where(IngestItem.job_id == job_id).order_by(IngestItem.position))).scalars()]
    figures = [figure_out(f) for f in (await db.execute(select(IngestFigure).where(IngestFigure.job_id == job_id).order_by(IngestFigure.id))).scalars()]
    job = await db.get(Job, job_id)
    results = [g for g in ((job.result or {}).get("groups") or [])] if job is not None else []
    return {
        "upload": {"id": up.id, "target_course_id": up.target_course_id, "target_chapter_id": up.target_chapter_id, "note": up.note} if up else None,
        "files": files, "items": items, "figures": figures, "results": results,
    }


@router.get("/courses/{course_id}/sources")
async def course_sources(course_id: int, db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (
        await db.execute(
            select(SourceLink, SourceFile, Chapter.title)
            .join(SourceFile, SourceFile.id == SourceLink.source_file_id)
            .outerjoin(Chapter, Chapter.id == SourceLink.chapter_id)
            .where(SourceLink.course_id == course_id)
            .order_by(SourceFile.id.desc())
        )
    ).all()
    by_file: dict[int, dict] = {}
    for link, sf, ch_title in rows:
        d = by_file.setdefault(sf.id, {**source_out(sf), "chapters": []})
        if link.chapter_id and not any(c["id"] == link.chapter_id for c in d["chapters"]):
            d["chapters"].append({"id": link.chapter_id, "title": ch_title})
    return list(by_file.values())


@router.get("/courses/{course_id}/chapters/{chapter_id}/sources")
async def chapter_sources(course_id: int, chapter_id: int, db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (
        await db.execute(
            select(SourceLink, SourceFile)
            .join(SourceFile, SourceFile.id == SourceLink.source_file_id)
            .where(SourceLink.course_id == course_id, SourceLink.chapter_id == chapter_id)
            .order_by(SourceFile.id.desc())
        )
    ).all()
    return [
        {"id": link.id, "source_file_id": sf.id, "name": sf.name, "kind": sf.kind, "size": sf.size, "created_at": sf.created_at, "pages": sf.pages}
        for link, sf in rows
    ]
