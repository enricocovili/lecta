"""Project files, compile, draft PDF and SyncTeX (admin)."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..db import get_db
from ..models import Build, Course
from ..security.auth import require_admin
from ..services import blobs, compile as compile_svc, latex, projects
from ..services.sniff import sniff_bytes
from .courses import load_course

router = APIRouter(dependencies=[Depends(require_admin)])

MAX_IMAGE_UPLOAD = 50 * 1024 * 1024
IMAGE_TYPES = {"png": ".png", "jpeg": ".jpg", "pdf": ".pdf"}


def file_out(pf) -> dict[str, Any]:  # noqa: ANN001
    return {
        "path": pf.path,
        "size": pf.size,
        "is_text": pf.is_text,
        "blob": pf.blob,
        "updated_at": pf.updated_at,
        "meta": pf.meta or {},
    }


@router.get("/courses/{course_id}/files")
async def list_files(course_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    files = [file_out(pf) for pf in await projects.get_files(db, c.id)]
    return {"files": files, "generated": ["preamble.tex"]}


@router.get("/courses/{course_id}/files/content")
async def get_content(course_id: int, path: str = Query(max_length=300), db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    if path == "preamble.tex":
        return {"path": path, "content": await projects.preamble_for(db, c), "blob": None, "generated": True,
                "overridden": bool(c.preamble_override)}
    pf = await projects.get_file(db, c.id, path)
    if pf is None:
        raise HTTPException(status_code=404, detail="Not Found")
    if not pf.is_text:
        raise HTTPException(status_code=415, detail="file binario; usa /files/raw")
    return {"path": pf.path, "content": pf.text_content, "blob": pf.blob, "updated_at": pf.updated_at}


@router.get("/courses/{course_id}/files/raw")
async def get_raw(course_id: int, path: str = Query(max_length=300), db: AsyncSession = Depends(get_db)) -> Response:
    c = await load_course(db, course_id)
    pf = await projects.get_file(db, c.id, path)
    if pf is None:
        raise HTTPException(status_code=404, detail="Not Found")
    ext = path.rsplit(".", 1)[-1].lower()
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "pdf": "application/pdf"}.get(ext, "text/plain; charset=utf-8")
    return Response(blobs.read_bytes(pf.blob), media_type=media, headers={"Content-Disposition": "inline"})


class SaveIn(BaseModel):
    path: str = Field(max_length=300)
    content: str = Field(max_length=5 * 1024 * 1024)
    base_blob: str | None = Field(None, max_length=64)
    force: bool = False


@router.put("/courses/{course_id}/files/content")
async def save_content(course_id: int, body: SaveIn, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    # Serialise with AI imports writing into the same course (they lock the course row too).
    await db.execute(select(Course.id).where(Course.id == c.id).with_for_update())
    if body.path == "preamble.tex":
        c.preamble_override = body.content
        c.updated_at = datetime.now(UTC)
        await db.commit()
        return {"path": body.path, "blob": None, "saved": True, "overridden": True}
    path = projects.validate_path(body.path)
    pf = await projects.get_file(db, c.id, path)
    if pf is not None and body.base_blob and pf.blob != body.base_blob and not body.force:
        raise HTTPException(
            status_code=409,
            detail={"message": "Il file è cambiato da quando l'hai aperto", "current_blob": pf.blob, "current": pf.text_content},
        )
    if pf is not None and pf.text_content == body.content:
        return {"path": path, "blob": pf.blob, "saved": False}
    pf = await projects.write_file(db, c, path, body.content.encode(), validated=True)
    await db.commit()
    return {"path": path, "blob": pf.blob, "saved": True}


class CreateIn(BaseModel):
    path: str = Field(max_length=300)
    content: str = Field("", max_length=5 * 1024 * 1024)


@router.post("/courses/{course_id}/files", status_code=201)
async def create_file(course_id: int, body: CreateIn, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    path = projects.validate_path(body.path)
    if not projects.is_text_path(path):
        raise HTTPException(status_code=400, detail="per i file binari usa il caricamento")
    if await projects.get_file(db, c.id, path):
        raise HTTPException(status_code=409, detail="il file esiste già")
    if path.startswith("chapters/"):
        raise HTTPException(status_code=400, detail="crea i capitoli dalla pagina della materia (servono un \\include)")
    content = body.content
    if not content and path.startswith("figures/"):
        content = "\\begin{tikzpicture}\n  \\draw[->] (0,0) -- (2,1);\n\\end{tikzpicture}\n"
    pf = await projects.write_file(db, c, path, content.encode(), validated=True)
    await db.commit()
    return file_out(pf)


class RenameIn(BaseModel):
    old: str = Field(max_length=300)
    new: str = Field(max_length=300)


@router.post("/courses/{course_id}/files/rename")
async def rename_file(course_id: int, body: RenameIn, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    if body.old.startswith("chapters/") or body.old == "main.tex":
        raise HTTPException(status_code=400, detail="rinomina i capitoli dalla pagina della materia")
    pf = await projects.rename_file(db, c, body.old, body.new)
    await db.commit()
    return file_out(pf)


class DeleteIn(BaseModel):
    path: str = Field(max_length=300)


@router.post("/courses/{course_id}/files/delete")
async def delete_file(course_id: int, body: DeleteIn, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    if body.path.startswith("chapters/") or body.path == "main.tex":
        raise HTTPException(status_code=400, detail="elimina i capitoli dalla pagina della materia")
    if not await projects.get_file(db, c.id, body.path):
        raise HTTPException(status_code=404, detail="Not Found")
    await projects.delete_file(db, c, body.path)
    await db.commit()
    return {"ok": True}


@router.post("/courses/{course_id}/files/upload")
async def upload_image(
    course_id: int, request: Request, name: str = Query(max_length=120), db: AsyncSession = Depends(get_db)
) -> dict:
    """Upload an image into images/ (raw body, validated by magic bytes)."""
    c = await load_course(db, course_id)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_IMAGE_UPLOAD:
            raise HTTPException(status_code=413, detail="immagine troppo grande")
    kind = sniff_bytes(bytes(data[:64]))
    if kind not in IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="solo immagini PNG, JPEG o PDF")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    from ..services.texttools import slugify

    path = projects.validate_path(f"images/{slugify(stem, 60)}{IMAGE_TYPES[kind]}")
    pf = await projects.write_file(db, c, path, bytes(data), validated=True, meta={"origin": "upload"})
    await db.commit()
    return file_out(pf)


# --------------------------------------------------------------------------- compile


class CompileIn(BaseModel):
    file: str | None = Field(None, max_length=300)
    full: bool = False


@router.post("/courses/{course_id}/compile")
async def compile_course(course_id: int, body: CompileIn, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    try:
        res = await compile_svc.run_build(db, c, file=body.file, full=body.full, priority="interactive")
    except latex.CompileServiceError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    res.pop("workdir", None)
    res.pop("log", None)
    res["pdf_url"] = f"/api/courses/{c.id}/pdf?b={res.get('build_id', 0)}" if res.get("pdf") else None
    return res


@router.get("/courses/{course_id}/builds/latest")
async def latest_build(course_id: int, kind: str = "draft", db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    b = (
        await db.execute(
            select(Build).where(Build.course_id == c.id, Build.kind == kind).order_by(Build.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if b is None:
        return {"build": None}
    return {
        "build": {
            "id": b.id,
            "status": b.status,
            "engine": b.engine,
            "includeonly": b.includeonly,
            "seconds": b.seconds,
            "diagnostics": b.diagnostics,
            "figures": b.figures,
            "created_at": b.created_at,
            "pdf_url": f"/api/courses/{c.id}/pdf?b={b.id}" if b.pdf_path else None,
        }
    }


@router.get("/courses/{course_id}/builds/{build_id}/log")
async def build_log(course_id: int, build_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    b = await db.get(Build, build_id)
    if b is None or b.course_id != course_id:
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(b.log, media_type="text/plain; charset=utf-8")


def stream_output(path, filename: str) -> StreamingResponse:  # noqa: ANN001
    """Stream a file written by the (untrusted) compile container without following symlinks."""
    root = config.latex_root.resolve()
    try:
        real = path.resolve(strict=True)
        if not str(real).startswith(str(root) + os.sep):
            raise OSError
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        raise HTTPException(status_code=404, detail="Not Found") from e
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise HTTPException(status_code=404, detail="Not Found")
    f = os.fdopen(fd, "rb")

    def gen():
        with f:
            while chunk := f.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        gen(),
        media_type="application/pdf",
        headers={"Content-Length": str(st.st_size), "Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/courses/{course_id}/pdf")
async def draft_pdf(course_id: int, db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    c: Course = await load_course(db, course_id)
    return stream_output(compile_svc.draft_pdf_path(c.id), f"{c.slug}-draft.pdf")


class SynctexIn(BaseModel):
    page: int = Field(ge=1)
    x: float
    y: float


@router.post("/courses/{course_id}/synctex")
async def synctex(course_id: int, body: SynctexIn, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    wd = projects.work_dir(c.id, "draft")
    try:
        res = await latex.synctex({"workdir": latex.rel(wd), "page": body.page, "x": body.x, "y": body.y})
    except latex.CompileServiceError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    f = res.get("file")
    if f:
        f = os.path.normpath(f)
        wd_s = str(wd.resolve())
        if f.startswith(wd_s):
            f = f[len(wd_s) :].lstrip("/")
        f = f.removeprefix("./")
        res["file"] = f
    return res
