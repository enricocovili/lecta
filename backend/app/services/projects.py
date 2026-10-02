"""Course LaTeX projects: file tree, chapters and materialisation.

File contents live in the content-addressed blob store; `project_files` maps
paths to blobs.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..models import Chapter, Course, ProjectFile
from . import blobs, tables, templates
from .settings import get_section
from .texttools import latex_to_text, slugify

TEXT_EXT = {".tex", ".bib", ".sty", ".cls", ".bst", ".txt", ".md", ".csv", ".dat", ".tikz"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".pdf"}
ALLOWED_EXT = TEXT_EXT | BINARY_EXT
# Generated at build time or dangerous for the build tool (latexmk rc files run Perl).
RESERVED_NAMES = {"preamble.tex", "latexmkrc", "_root.tex", "_figure.tex"}
RESERVED_PREFIXES = ("figures-cache/", "_")
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$")
MAX_TEXT_BYTES = 5 * 1024 * 1024
MAX_BINARY_BYTES = 50 * 1024 * 1024
SYNC_MANIFEST = ".lecta-sync.json"


class ProjectError(HTTPException):
    def __init__(self, detail: str, status: int = 400):
        super().__init__(status_code=status, detail=detail)


def validate_path(path: str) -> str:
    p = (path or "").strip().replace("\\", "/")
    parts = PurePosixPath(p).parts
    if not parts or p.startswith("/") or len(parts) > 4 or len(p) > 250:
        raise ProjectError("invalid path")
    for part in parts:
        if part in (".", "..") or not _COMPONENT_RE.match(part):
            raise ProjectError(f"invalid path component: {part!r}")
    norm = "/".join(parts)
    if PurePosixPath(norm).suffix.lower() not in ALLOWED_EXT:
        raise ProjectError("file type not allowed in a LaTeX project")
    if parts[-1].lower() in RESERVED_NAMES or norm.startswith(RESERVED_PREFIXES):
        raise ProjectError("reserved file name")
    return norm


def is_text_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in TEXT_EXT


# --------------------------------------------------------------------------- files


async def get_files(db: AsyncSession, course_id: int) -> list[ProjectFile]:
    rows = await db.execute(select(ProjectFile).where(ProjectFile.course_id == course_id).order_by(ProjectFile.path))
    return list(rows.scalars())


async def get_file(db: AsyncSession, course_id: int, path: str) -> ProjectFile | None:
    return (
        await db.execute(select(ProjectFile).where(ProjectFile.course_id == course_id, ProjectFile.path == path))
    ).scalar_one_or_none()


async def manifest(db: AsyncSession, course_id: int) -> dict[str, str]:
    rows = await db.execute(select(ProjectFile.path, ProjectFile.blob).where(ProjectFile.course_id == course_id))
    return {p: b for p, b in rows.all()}


async def _update_tsv(db: AsyncSession, file_id: int, lang: str, content: str | None) -> None:
    await db.execute(
        text("UPDATE project_files SET tsv = to_tsvector(CAST(:cfg AS regconfig), :t) WHERE id = :id"),
        {"cfg": templates.ts_config(lang), "t": latex_to_text(content or "")[:500000], "id": file_id},
    )


async def write_file(
    db: AsyncSession,
    course: Course,
    path: str,
    data: bytes,
    *,
    meta: dict[str, Any] | None = None,
    validated: bool = False,
) -> ProjectFile:
    if not validated:
        path = validate_path(path)
    is_text = is_text_path(path)
    content: str | None = None
    if is_text:
        if len(data) > MAX_TEXT_BYTES:
            raise ProjectError("text file too large")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ProjectError("text files must be UTF-8") from e
        if "\x00" in content:
            raise ProjectError("text files must not contain NUL bytes")
    elif len(data) > MAX_BINARY_BYTES:
        raise ProjectError("file too large")
    h = blobs.put_bytes(data)
    pf = await get_file(db, course.id, path)
    now = datetime.now(UTC)
    if pf is None:
        pf = ProjectFile(
            course_id=course.id, path=path, blob=h, size=len(data), is_text=is_text, text_content=content,
            meta=meta or {}, updated_at=now,
        )
        db.add(pf)
    else:
        pf.blob, pf.size, pf.is_text, pf.text_content, pf.updated_at = h, len(data), is_text, content, now
        if meta is not None:
            pf.meta = meta
    await db.flush()
    if is_text and path.endswith(".tex"):
        await _update_tsv(db, pf.id, course.language, content)
    course.updated_at = now
    return pf


async def delete_file(db: AsyncSession, course: Course, path: str) -> None:
    await db.execute(delete(ProjectFile).where(ProjectFile.course_id == course.id, ProjectFile.path == path))
    course.updated_at = datetime.now(UTC)


async def rename_file(db: AsyncSession, course: Course, old: str, new: str) -> ProjectFile:
    new = validate_path(new)
    pf = await get_file(db, course.id, old)
    if pf is None:
        raise ProjectError("file not found", 404)
    if await get_file(db, course.id, new):
        raise ProjectError("a file with that name already exists", 409)
    pf.path = new
    pf.updated_at = datetime.now(UTC)
    course.updated_at = pf.updated_at
    await db.flush()
    return pf


async def read_text(db: AsyncSession, course_id: int, path: str) -> str | None:
    pf = await get_file(db, course_id, path)
    if pf is None:
        return None
    return pf.text_content if pf.is_text else None


# --------------------------------------------------------------------------- courses & chapters


async def unique_course_slug(db: AsyncSession, name: str, wanted: str | None = None) -> str:
    base = slugify(wanted or name)
    slug, i = base, 2
    while (await db.execute(select(Course.id).where(Course.slug == slug))).first():
        slug, i = f"{base}-{i}", i + 1
    return slug


async def create_course(
    db: AsyncSession,
    *,
    name: str,
    slug: str | None = None,
    academic_year: str | None = None,
    language: str = "it",
    tags: list[str] | None = None,
    description: str | None = None,
    chapter_titles: list[str] | None = None,
) -> Course:
    course = Course(
        name=name,
        slug=await unique_course_slug(db, name, slug),
        academic_year=academic_year,
        language=language,
        tags=tags or [],
        description=description,
    )
    db.add(course)
    await db.flush()
    paths = []
    for i, title in enumerate(chapter_titles or [], start=1):
        ch_slug = await _unique_chapter_slug(db, course.id, title)
        path = f"chapters/{i:02d}-{ch_slug}.tex"
        db.add(Chapter(course_id=course.id, position=i, slug=ch_slug, title=title, path=path))
        await write_file(db, course, path, templates.chapter_tex(title, ch_slug).encode(), validated=True)
        paths.append(path)
    await write_file(
        db, course, "main.tex", templates.main_tex(name, academic_year, language, paths).encode(), validated=True
    )
    await db.flush()
    return course


async def chapters_of(db: AsyncSession, course_id: int) -> list[Chapter]:
    rows = await db.execute(select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.position))
    return list(rows.scalars())


async def _unique_chapter_slug(db: AsyncSession, course_id: int, title: str, wanted: str | None = None) -> str:
    base = slugify(wanted or title, 40)
    slug, i = base, 2
    while (await db.execute(select(Chapter.id).where(Chapter.course_id == course_id, Chapter.slug == slug))).first():
        slug, i = f"{base}-{i}", i + 1
    return slug


async def _renumber(db: AsyncSession, course: Course, ordered: list[Chapter]) -> list[str]:
    """Assign positions 1..n and NN-slug paths; rename files; rewrite main.tex."""
    changed: list[str] = []
    # Two passes so that swapping two chapters never collides on a path.
    for ch in ordered:
        await db.execute(
            update(ProjectFile)
            .where(ProjectFile.course_id == course.id, ProjectFile.path == ch.path)
            .values(path="_renaming/" + ch.path)
        )
    await db.flush()
    for i, ch in enumerate(ordered, start=1):
        new_path = f"chapters/{i:02d}-{ch.slug}.tex"
        await db.execute(
            update(ProjectFile)
            .where(ProjectFile.course_id == course.id, ProjectFile.path == "_renaming/" + ch.path)
            .values(path=new_path)
        )
        if ch.path != new_path:
            changed += [ch.path, new_path]
        ch.position, ch.path = i, new_path
    await db.flush()
    changed += await rewrite_main_block(db, course, [c.path for c in ordered])
    return changed


async def rewrite_main_block(db: AsyncSession, course: Course, paths: list[str] | None = None) -> list[str]:
    if paths is None:
        paths = [c.path for c in await chapters_of(db, course.id)]
    main = await read_text(db, course.id, "main.tex")
    if main is None:
        main = templates.main_tex(course.name, course.academic_year, course.language, paths)
    new = templates.replace_chapter_block(main, paths)
    if new != main:
        await write_file(db, course, "main.tex", new.encode(), validated=True)
        return ["main.tex"]
    return []


async def add_chapter(
    db: AsyncSession,
    course: Course,
    title: str,
    *,
    position: int | None = None,
    content: str | None = None,
    slug: str | None = None,
) -> Chapter:
    chapters = await chapters_of(db, course.id)
    ch_slug = await _unique_chapter_slug(db, course.id, title, slug)
    tmp_path = f"chapters/new-{ch_slug}.tex"
    ch = Chapter(course_id=course.id, position=0, slug=ch_slug, title=title, path=tmp_path)
    db.add(ch)
    await write_file(
        db, course, tmp_path, (content if content is not None else templates.chapter_tex(title, ch_slug)).encode(),
        validated=True,
    )
    idx = len(chapters) if position is None else max(0, min(position - 1, len(chapters)))
    chapters.insert(idx, ch)
    await db.flush()
    await _renumber(db, course, chapters)
    return ch


async def delete_chapter(db: AsyncSession, course: Course, ch: Chapter) -> None:
    await delete_file(db, course, ch.path)
    await db.delete(ch)
    await db.flush()
    await _renumber(db, course, await chapters_of(db, course.id))


async def reorder_chapters(db: AsyncSession, course: Course, ids: list[int]) -> None:
    chapters = await chapters_of(db, course.id)
    by_id = {c.id: c for c in chapters}
    if sorted(ids) != sorted(by_id):
        raise ProjectError("reorder must list every chapter exactly once")
    await _renumber(db, course, [by_id[i] for i in ids])


async def update_chapter(db: AsyncSession, course: Course, ch: Chapter, *, title: str | None = None, slug: str | None = None) -> None:
    if title or slug:
        course.updated_at = datetime.now(UTC)
    if title and title != ch.title:
        src = await read_text(db, course.id, ch.path)
        if src is not None:
            old = "\\chapter{" + templates.tex_escape(ch.title) + "}"
            if old in src:
                src = src.replace(old, "\\chapter{" + templates.tex_escape(title) + "}", 1)
                await write_file(db, course, ch.path, src.encode(), validated=True)
        ch.title = title
    if slug and slugify(slug, 40) != ch.slug:
        ch.slug = await _unique_chapter_slug(db, course.id, slug, slug)
        await _renumber(db, course, await chapters_of(db, course.id))
    ch.updated_at = datetime.now(UTC)
    await db.flush()


# --------------------------------------------------------------------------- preamble & materialise


async def preamble_for(db: AsyncSession, course: Course) -> str:
    if course.preamble_override:
        return course.preamble_override
    tpl = await get_section(db, "template")
    return tpl.preamble or templates.DEFAULT_PREAMBLE


async def engine_for(db: AsyncSession, course: Course) -> str:
    if course.engine:
        return course.engine
    return (await get_section(db, "latex")).engine


def work_dir(course_id: int, name: str = "draft") -> Path:
    if not re.match(r"^[a-z0-9-]{1,60}$", name):
        raise ValueError("bad work dir name")
    return config.latex_work_dir / str(course_id) / name


def _safe_join(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if not str(p).startswith(str(root.resolve()) + os.sep):
        raise ProjectError("path escapes the project")
    return p


def materialize_files(dest: Path, files: dict[str, bytes | str]) -> list[str]:
    """Write `files` (path → content, or path → blob hash prefixed 'blob:') into
    dest, touching only changed files so latexmk can rebuild incrementally.
    Files from a previous sync that are gone are removed; build outputs are kept."""
    dest.mkdir(parents=True, exist_ok=True)
    man_path = dest / SYNC_MANIFEST
    try:
        old: dict[str, str] = json.loads(man_path.read_text())
    except (OSError, ValueError):
        old = {}
    new: dict[str, str] = {}
    written: list[str] = []
    for rel, content in files.items():
        if isinstance(content, str) and content.startswith("blob:"):
            h = content[5:]
            data = None
        else:
            data = content.encode() if isinstance(content, str) else content
            h = blobs.sha256_bytes(data)
        new[rel] = h
        target = _safe_join(dest, rel)
        if old.get(rel) == h and target.is_file() and not target.is_symlink():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.is_dir():
            target.unlink()
        if data is None:
            data = blobs.read_bytes(h)
        tmp = target.with_name(target.name + ".lecta-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        written.append(rel)
    for rel in set(old) - set(new):
        try:
            p = _safe_join(dest, rel)
            if p.is_file() or p.is_symlink():
                p.unlink()
        except (OSError, ProjectError):
            pass
    man_path.write_text(json.dumps(new))
    return written


async def materialize(
    db: AsyncSession, course: Course, dest: Path, overlay: dict[str, bytes | None] | None = None
) -> list[str]:
    files: dict[str, bytes | str] = {p: "blob:" + h for p, h in (await manifest(db, course.id)).items()}
    for p, data in (overlay or {}).items():
        if data is None:
            files.pop(p, None)
        else:
            files[p] = data
    # The PDF shows a table as a table even when its author left the rules out (see services/tables).
    for p, v in list(files.items()):
        if isinstance(v, str) and v.startswith("blob:") and p.startswith("chapters/") and p.endswith(".tex"):
            try:
                text = blobs.read_text(v[5:])
            except (OSError, UnicodeDecodeError):
                continue
            ruled = tables.add_rules(text)
            if ruled != text:
                files[p] = ruled.encode()
    files["preamble.tex"] = templates.with_compat(await preamble_for(db, course)).encode()
    return materialize_files(dest, files)


def safe_read_output(path: Path, max_bytes: int = 512 * 1024 * 1024) -> bytes | None:
    """Read a build output written by the compile container.

    The compile container is untrusted: refuse symlinks and anything resolving
    outside the latex work dir, so a compromised TeX run can't make us serve
    files from this container.
    """
    root = config.latex_work_dir.resolve()
    try:
        real = path.resolve(strict=True)
    except OSError:
        return None
    if not str(real).startswith(str(root) + os.sep):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        import stat as _stat

        if not _stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
            return None
        return f.read()
