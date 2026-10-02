"""Uploads: create, stream files (raw bodies, never buffered), finish → ingestion job.

Also: source files (originals, pages) and ingestion manifests.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..db import get_db
from ..models import Chapter, Course, IngestFigure, IngestItem, InboxItem, Job, SourceFile, SourceLink, Upload
from ..pipeline import compose
from ..pipeline.unpack import classify, is_junk
from ..security.auth import require_admin
from ..services import blobs
from ..services import jobs as jobs_svc
from ..services import settings as settings_svc

router = APIRouter(dependencies=[Depends(require_admin)])

CHUNK = 1024 * 1024


def upload_dir(upload_id: int) -> Path:
    return config.data_dir / "uploads" / str(upload_id)


class UploadIn(BaseModel):
    course_id: int | None = None
    chapter_id: int | None = None
    note: str | None = Field(None, max_length=2000)
    via: str = Field("web", pattern="^(web|quick)$")


@router.post("/uploads", status_code=201)
async def create_upload(body: UploadIn, db: AsyncSession = Depends(get_db)) -> dict:
    if body.chapter_id:
        ch = await db.get(Chapter, body.chapter_id)
        if ch is None:
            raise HTTPException(status_code=404, detail="Not Found")
        body.course_id = ch.course_id
    if body.course_id and await db.get(Course, body.course_id) is None:
        raise HTTPException(status_code=404, detail="Not Found")
    up = Upload(target_course_id=body.course_id, target_chapter_id=body.chapter_id, note=body.note, via=body.via)
    db.add(up)
    await db.commit()
    upload_dir(up.id).mkdir(parents=True, exist_ok=True)
    return {"id": up.id}


def _clean_rel(path: str | None) -> tuple[str, str]:
    """(folder, name) from a client-supplied relative path (untrusted, used only as a hint)."""
    p = (path or "").replace("\\", "/")
    parts = [x for x in PurePosixPath(p).parts if x not in ("", ".", "..", "/")]
    parts = [re.sub(r"[\x00-\x1f]", "", x)[:200] for x in parts] or ["file"]
    return "/".join(parts[:-1])[:500], parts[-1]


@router.post("/uploads/{upload_id}/files")
async def upload_file(
    upload_id: int,
    request: Request,
    name: str = Query(max_length=1000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Body = the raw file. Streamed to disk with a size limit; type detected by magic bytes."""
    up = await db.get(Upload, upload_id)
    if up is None:
        raise HTTPException(status_code=404, detail="Not Found")
    if up.status != "receiving":
        raise HTTPException(status_code=409, detail="caricamento già concluso")
    limits = await settings_svc.get_section(db, "uploads")
    max_bytes = limits.max_file_mb * 1024 * 1024
    folder, base = _clean_rel(name)
    d = upload_dir(upload_id) / "incoming"
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
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="file vuoto")
    parts = tuple(x for x in (folder.split("/") if folder else [])) + (base,)
    if is_junk(parts):
        tmp.unlink(missing_ok=True)
        return {"skipped": True, "reason": "system file"}
    kind, mime = classify(tmp, base)
    h = blobs.put_file(tmp, move=True)
    sf = SourceFile(
        upload_id=upload_id, name=base, folder=folder or None, kind=kind, mime=mime, size=size, blob=h,
        status="ok" if kind != "unsupported" else "unsupported",
        reason=None if kind != "unsupported" else "unsupported file type",
    )
    db.add(sf)
    up.file_count += 1
    up.total_size += size
    await db.commit()
    return {"id": sf.id, "kind": kind, "size": size, "name": base, "status": sf.status}


class FinishIn(BaseModel):
    title: str | None = Field(None, max_length=200)
    # Go on even without a notes file (.md/.txt): the text is then a summary of the material alone.
    without_notes: bool = False
    # What the student wants from the text of the subject (only with a chosen subject): None = as saved for the course,
    # "" = none (the AI decides). With save_guidelines the text is kept as the course's guidelines.
    guidelines: str | None = Field(None, max_length=8000)
    save_guidelines: bool = False


def _zip_has_notes(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist()[:20000]:
                parts = tuple(p for p in PurePosixPath(info.filename.replace("\\", "/")).parts if p not in ("", "."))
                if not info.is_dir() and parts and not is_junk(parts) and compose.is_notes_name(parts[-1]) and "." in parts[-1]:
                    return True
    except (zipfile.BadZipFile, OSError, ValueError):
        pass
    return False


async def has_notes(db: AsyncSession, upload_id: int) -> bool:
    """Whether the upload holds at least one file of class notes (.md/.txt), also inside zips."""
    files = (await db.execute(select(SourceFile).where(SourceFile.upload_id == upload_id, SourceFile.status == "ok"))).scalars().all()
    if any(f.kind in ("markdown", "text") and compose.is_notes_name(f.name) for f in files):
        return True
    return any(f.kind == "zip" and f.blob and _zip_has_notes(blobs.path_for(f.blob)) for f in files)


@router.post("/uploads/{upload_id}/finish")
async def finish_upload(upload_id: int, body: FinishIn, db: AsyncSession = Depends(get_db)) -> dict:
    up = await db.get(Upload, upload_id)
    if up is None:
        raise HTTPException(status_code=404, detail="Not Found")
    if up.status != "receiving":
        raise HTTPException(status_code=409, detail="caricamento già concluso")
    if up.file_count == 0:
        raise HTTPException(status_code=400, detail="nessun file caricato")
    # Quick uploads are photos of handwritten notes: they are the notes.
    if up.via != "quick" and not body.without_notes and not await has_notes(db, up.id):
        raise HTTPException(status_code=409, detail={
            "code": "no_notes",
            "message": "Nel caricamento non c'è un file di appunti (.md o .txt): senza, il testo sarà un riassunto delle sole slide.",
        })
    names = (await db.execute(select(SourceFile.name).where(SourceFile.upload_id == up.id).limit(3))).scalars().all()
    title = body.title or ("Caricamento: " + ", ".join(names) + ("…" if up.file_count > 3 else ""))
    up.status = "queued"
    payload: dict[str, Any] = {"upload_id": up.id}
    if body.guidelines is not None:
        text = body.guidelines.strip()
        payload["guidelines"] = text
        course = await db.get(Course, up.target_course_id) if up.target_course_id else None
        if course is not None and body.save_guidelines:
            course.guidelines = text or None
    job = await jobs_svc.enqueue(
        db, "ingest", payload, title=title[:200], priority="ingest", course_id=up.target_course_id, commit=False
    )
    up.job_id = job.id
    await db.commit()
    return {"job_id": job.id, "upload_id": up.id}


@router.get("/uploads")
async def list_uploads(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(Upload).order_by(Upload.id.desc()).limit(100))).scalars()
    return [
        {"id": u.id, "status": u.status, "job_id": u.job_id, "file_count": u.file_count, "total_size": u.total_size,
         "created_at": u.created_at, "target_course_id": u.target_course_id, "via": u.via}
        for u in rows
    ]


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
    inbox_for = (await db.execute(select(InboxItem).where(InboxItem.assigned_job_id == job_id))).scalar_one_or_none()
    files = []
    if up:
        files = [source_out(s) for s in (await db.execute(select(SourceFile).where(SourceFile.upload_id == up.id).order_by(SourceFile.id))).scalars()]
    items = [item_out(i) for i in (await db.execute(select(IngestItem).where(IngestItem.job_id == job_id).order_by(IngestItem.position))).scalars()]
    figures = [figure_out(f) for f in (await db.execute(select(IngestFigure).where(IngestFigure.job_id == job_id).order_by(IngestFigure.id))).scalars()]
    job = await db.get(Job, job_id)
    results = [g for g in ((job.result or {}).get("groups") or [])] if job is not None else []
    inbox = [
        {"id": i.id, "title": i.title, "status": i.status}
        for i in (await db.execute(select(InboxItem).where(InboxItem.job_id == job_id))).scalars()
    ]
    return {
        "upload": {"id": up.id, "target_course_id": up.target_course_id, "target_chapter_id": up.target_chapter_id, "note": up.note} if up else None,
        "inbox_source": {"id": inbox_for.id, "title": inbox_for.title} if inbox_for else None,
        "files": files, "items": items, "figures": figures, "results": results, "inbox": inbox,
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
