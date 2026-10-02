"""Courses and chapters (admin)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Chapter, Course, ProjectFile, Publication
from ..security.auth import require_admin
from ..services import overview, projects
from ..services.texttools import slugify

router = APIRouter(dependencies=[Depends(require_admin)])

LANG_PATTERN = r"^[a-z]{2,3}$"
GUIDELINES_MAX = 8000


class CourseIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(None, max_length=120)
    academic_year: str | None = Field(None, max_length=20)
    language: str = Field("it", pattern=LANG_PATTERN)
    tags: list[str] = Field(default_factory=list, max_length=30)
    description: str | None = Field(None, max_length=5000)
    chapters: list[str] = Field(default_factory=list, max_length=200)


class CoursePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, max_length=120)
    academic_year: str | None = Field(None, max_length=20)
    language: str | None = Field(None, pattern=LANG_PATTERN)
    tags: list[str] | None = Field(None, max_length=30)
    description: str | None = Field(None, max_length=5000)
    engine: str | None = Field(None, pattern=r"^(pdflatex|xelatex|lualatex|default)$")
    preamble_override: str | None = Field(None, max_length=200000)
    clear_preamble_override: bool = False
    # How the student wants the subject's text written; empty clears it (the AI then decides).
    guidelines: str | None = Field(None, max_length=GUIDELINES_MAX)


class ChapterIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    slug: str | None = Field(None, max_length=120)
    position: int | None = Field(None, ge=1)


class ChapterPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    slug: str | None = Field(None, max_length=120)


class ReorderIn(BaseModel):
    ids: list[int]


def chapter_out(ch: Chapter) -> dict[str, Any]:
    return {
        "id": ch.id,
        "course_id": ch.course_id,
        "position": ch.position,
        "slug": ch.slug,
        "title": ch.title,
        "path": ch.path,
        "updated_at": ch.updated_at,
    }


def course_out(c: Course, chapters: list[Chapter] | None = None, pub: Publication | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": c.id,
        "name": c.name,
        "slug": c.slug,
        "academic_year": c.academic_year,
        "language": c.language,
        "tags": c.tags,
        "description": c.description,
        "engine": c.engine,
        "has_preamble_override": bool(c.preamble_override),
        "guidelines": c.guidelines or "",
        "published": c.published,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }
    if chapters is not None:
        out["chapters"] = [chapter_out(ch) for ch in chapters]
    if pub is not None:
        out["publication"] = {"id": pub.id, "created_at": pub.created_at, "pdf_size": pub.pdf_size}
    return out


async def load_course(db: AsyncSession, course_id: int) -> Course:
    c = await db.get(Course, course_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return c


async def load_chapter(db: AsyncSession, course: Course, chapter_id: int) -> Chapter:
    ch = await db.get(Chapter, chapter_id)
    if ch is None or ch.course_id != course.id:
        raise HTTPException(status_code=404, detail="Not Found")
    return ch


@router.get("/courses")
async def list_courses(db: AsyncSession = Depends(get_db)) -> list[dict]:
    courses = (await db.execute(select(Course).order_by(Course.updated_at.desc()))).scalars().all()
    counts = dict(
        (await db.execute(select(Chapter.course_id, func.count()).group_by(Chapter.course_id))).all()
    )
    out = []
    for c in courses:
        d = course_out(c)
        d["chapter_count"] = counts.get(c.id, 0)
        out.append(d)
    return out


@router.post("/courses", status_code=201)
async def create_course(body: CourseIn, db: AsyncSession = Depends(get_db)) -> dict:
    course = await projects.create_course(
        db,
        name=body.name,
        slug=body.slug,
        academic_year=body.academic_year,
        language=body.language,
        tags=body.tags,
        description=body.description,
        chapter_titles=body.chapters,
    )
    await db.commit()
    return course_out(course, await projects.chapters_of(db, course.id))


@router.get("/courses/{course_id}")
async def get_course(course_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    pub = await db.get(Publication, c.current_publication_id) if c.current_publication_id else None
    out = course_out(c, await projects.chapters_of(db, c.id), pub)
    out["preamble_override"] = c.preamble_override
    return out


@router.patch("/courses/{course_id}")
async def patch_course(course_id: int, body: CoursePatch, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    data = body.model_dump(exclude_unset=True)
    if "slug" in data and data["slug"]:
        new_slug = slugify(data["slug"])
        if new_slug != c.slug:
            c.slug = await projects.unique_course_slug(db, new_slug, new_slug)
    for key in ("name", "academic_year", "tags", "description"):
        if key in data:
            setattr(c, key, data[key])
    if data.get("language") and data["language"] != c.language:
        c.language = data["language"]
        # Re-index the full-text vectors with the new text-search configuration.
        for pf in await projects.get_files(db, c.id):
            if pf.is_text and pf.path.endswith(".tex"):
                await projects._update_tsv(db, pf.id, c.language, pf.text_content)
    if "guidelines" in data:
        c.guidelines = (data["guidelines"] or "").strip() or None
    if "engine" in data:
        c.engine = None if data["engine"] in (None, "default") else data["engine"]
    if body.clear_preamble_override:
        c.preamble_override = None
    elif data.get("preamble_override") is not None:
        c.preamble_override = data["preamble_override"]
    c.updated_at = datetime.now().astimezone()
    await db.commit()
    return await get_course(course_id, db)


class DeleteIn(BaseModel):
    confirm_slug: str


@router.post("/courses/{course_id}/delete")
async def delete_course(course_id: int, body: DeleteIn, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    if body.confirm_slug != c.slug:
        raise HTTPException(status_code=400, detail="Scrivi lo slug della materia per confermare")
    await db.delete(c)
    await db.commit()
    return {"ok": True}


@router.post("/courses/{course_id}/chapters", status_code=201)
async def add_chapter(course_id: int, body: ChapterIn, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    ch = await projects.add_chapter(db, c, body.title, position=body.position, slug=body.slug)
    await db.commit()
    return chapter_out(ch)


@router.patch("/courses/{course_id}/chapters/{chapter_id}")
async def patch_chapter(course_id: int, chapter_id: int, body: ChapterPatch, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    ch = await load_chapter(db, c, chapter_id)
    await projects.update_chapter(db, c, ch, title=body.title, slug=body.slug)
    await db.commit()
    return chapter_out(ch)


@router.delete("/courses/{course_id}/chapters/{chapter_id}")
async def delete_chapter(course_id: int, chapter_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    ch = await load_chapter(db, c, chapter_id)
    await projects.delete_chapter(db, c, ch)
    await db.commit()
    return {"ok": True}


@router.post("/courses/{course_id}/chapters/reorder")
async def reorder(course_id: int, body: ReorderIn, db: AsyncSession = Depends(get_db)) -> list[dict]:
    c = await load_course(db, course_id)
    await projects.reorder_chapters(db, c, body.ids)
    await db.commit()
    return [chapter_out(ch) for ch in await projects.chapters_of(db, c.id)]


@router.get("/tree")
async def tree(db: AsyncSession = Depends(get_db)) -> list[dict]:
    courses = (await db.execute(select(Course).order_by(Course.name))).scalars().all()
    chapters = (await db.execute(select(Chapter).order_by(Chapter.course_id, Chapter.position))).scalars().all()
    by_course: dict[int, list[dict]] = {}
    for ch in chapters:
        by_course.setdefault(ch.course_id, []).append(
            {"id": ch.id, "title": ch.title, "path": ch.path, "position": ch.position}
        )
    states = await overview.course_states(db)
    return [
        {
            "id": c.id,
            "name": c.name,
            "slug": c.slug,
            "published": c.published,
            "chapters": by_course.get(c.id, []),
            "status": states.get(c.id, {}).get("status", "ok"),
            "sources": states.get(c.id, {}).get("sources", 0),
            "pending_reviews": states.get(c.id, {}).get("pending_reviews", 0),
        }
        for c in courses
    ]


@router.get("/courses/{course_id}/chapters/{chapter_id}")
async def get_chapter(course_id: int, chapter_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    c = await load_course(db, course_id)
    ch = await load_chapter(db, c, chapter_id)
    out = chapter_out(ch)
    out["course"] = {"id": c.id, "name": c.name, "slug": c.slug}
    pf = await projects.get_file(db, c.id, ch.path)
    out["size"] = pf.size if pf else 0
    # Figures and images referenced from the chapter.
    files = (await db.execute(select(ProjectFile.path).where(ProjectFile.course_id == c.id))).scalars().all()
    src = (pf.text_content if pf else "") or ""
    out["figures"] = [p for p in files if p.startswith("figures/") and p.rsplit("/", 1)[-1].removesuffix(".tex") in src]
    out["images"] = [p for p in files if p.startswith("images/") and p.rsplit("/", 1)[-1] in src]
    return out
