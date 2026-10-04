"""Public (anonymous) API: published courses/chapters and their PDFs only.

Nothing here reads project files, sources or drafts; everything comes from
immutable `publications` snapshots.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Course, Publication
from ..services import blobs
from ..services import settings as settings_svc

router = APIRouter(prefix="/public")


def _nf() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


async def _published(db: AsyncSession) -> list[tuple[Course, Publication]]:
    rows = await db.execute(
        select(Course, Publication)
        .join(Publication, Publication.id == Course.current_publication_id)
        .where(Course.published.is_(True))
        .order_by(Publication.created_at.desc())
    )
    return [(c, p) for c, p in rows.all()]


def _chapters_out(slug: str, pub: Publication) -> list[dict]:
    return [
        {
            "slug": ch["slug"],
            "title": ch["title"],
            "position": ch["position"],
            "page_start": ch.get("page_start"),
            "page_end": ch.get("page_end"),
            "pdf_size": ch.get("pdf_size") if ch.get("pdf_blob") else None,
            "pdf_url": f"/api/public/courses/{slug}/chapters/{ch['slug']}.pdf" if ch.get("pdf_blob") else None,
        }
        for ch in pub.chapters
    ]


def _page_count(p: Publication) -> int | None:
    """Pages of the published PDF, as recorded by the chapter split (the last chapter ends on the last page)."""
    ends = [ch.get("page_end") for ch in p.chapters if ch.get("page_end")]
    return max(ends) if ends else None


def _course_out(c: Course, p: Publication, with_chapters: bool = False) -> dict:
    out = {
        "slug": c.slug,
        "name": p.title,
        "description": p.description,
        "academic_year": c.academic_year,
        "language": c.language,
        "tags": c.tags,
        "updated_at": p.created_at,
        "pdf_url": f"/api/public/courses/{c.slug}.pdf",
        "pdf_size": p.pdf_size,
        "source_url": f"/api/public/courses/{c.slug}/source.zip" if p.source_blob else None,
        "source_size": p.source_size if p.source_blob else None,
        "chapter_count": len(p.chapters),
        "page_count": _page_count(p),
    }
    if with_chapters:
        out["chapters"] = _chapters_out(c.slug, p)
    return out


@router.get("/site")
async def site(db: AsyncSession = Depends(get_db)) -> dict:
    s = await settings_svc.get_section(db, "site")
    return {"title": s.title, "description": s.description, "byline": s.byline, "allow_indexing": s.allow_indexing}


@router.get("/courses")
async def courses(db: AsyncSession = Depends(get_db)) -> list[dict]:
    return [_course_out(c, p) for c, p in await _published(db)]


async def _one(db: AsyncSession, slug: str) -> tuple[Course, Publication]:
    for c, p in await _published(db):
        if c.slug == slug:
            return c, p
    raise _nf()


def _pdf(h: str, filename: str) -> FileResponse:
    if not blobs.exists(h):
        raise _nf()
    return FileResponse(
        blobs.path_for(h),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "public, max-age=300",
        },
    )


@router.get("/courses/{slug}.pdf")
async def course_pdf(slug: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    c, p = await _one(db, slug)
    return _pdf(p.pdf_blob, f"{c.slug}.pdf")


@router.get("/courses/{slug}/source.zip")
async def course_source(slug: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    """The LaTeX project of the published version."""
    c, p = await _one(db, slug)
    if not p.source_blob or not blobs.exists(p.source_blob):
        raise _nf()
    return FileResponse(
        blobs.path_for(p.source_blob), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{c.slug}-latex.zip"', "Cache-Control": "public, max-age=300"},
    )


@router.get("/courses/{slug}/chapters/{chapter}.pdf")
async def chapter_pdf(slug: str, chapter: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    c, p = await _one(db, slug)
    for ch in p.chapters:
        if ch["slug"] == chapter and ch.get("pdf_blob"):
            return _pdf(ch["pdf_blob"], f"{c.slug}-{ch['slug']}.pdf")
    raise _nf()


@router.get("/courses/{slug}")
async def course(slug: str, db: AsyncSession = Depends(get_db)) -> dict:
    c, p = await _one(db, slug)
    return _course_out(c, p, with_chapters=True)


@router.get("/search")
async def search(q: str = Query("", max_length=200), db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Search over published course and chapter titles only."""
    terms = [t for t in q.lower().split() if t]
    if not terms:
        return []
    results = []
    for c, p in await _published(db):
        hay = f"{p.title} {c.academic_year or ''} {' '.join(c.tags or [])}".lower()
        if all(t in hay for t in terms):
            results.append({"type": "course", "course": c.slug, "title": p.title, "url": f"/courses/{c.slug}"})
        for ch in p.chapters:
            if all(t in ch["title"].lower() or t in p.title.lower() for t in terms) and any(
                t in ch["title"].lower() for t in terms
            ):
                results.append(
                    {
                        "type": "chapter",
                        "course": c.slug,
                        "course_title": p.title,
                        "title": ch["title"],
                        "url": f"/courses/{c.slug}#ch-{ch['slug']}",
                        "pdf_url": f"/api/public/courses/{c.slug}/chapters/{ch['slug']}.pdf"
                        if ch.get("pdf_blob")
                        else None,
                    }
                )
    return results[:100]
