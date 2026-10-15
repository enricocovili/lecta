"""Compile orchestration: sync project → compile service → diagnostics → Build row."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Build, Course
from . import latex, projects, texlog
from .settings import get_section

KEEP_BUILDS = 20


def _chapter_for(path: str | None, files: dict[str, str | None]) -> list[str] | None:
    """Which chapters to \\includeonly when editing `path` (None = full build)."""
    if not path:
        return None
    if path.startswith("chapters/") and path.endswith(".tex"):
        return [path]
    if path.startswith("figures/") and path.endswith(".tex"):
        name = path.removesuffix(".tex")
        pat = re.compile(r"\\input\s*\{\s*" + re.escape(name) + r"(?:\.tex)?\s*\}")
        users = [p for p, src in files.items() if p.startswith("chapters/") and src and pat.search(src)]
        return users or None
    return None


async def run_build(
    db: AsyncSession,
    course: Course,
    *,
    kind: str = "draft",
    workdir_name: str = "draft",
    file: str | None = None,
    full: bool = False,
    priority: str = "interactive",
    overlay: dict[str, bytes | None] | None = None,
    clean: bool = False,
    mode: str = "draft",
    record: bool = True,
) -> dict[str, Any]:
    wd = projects.work_dir(course.id, workdir_name)
    if clean and wd.exists():
        await asyncio.to_thread(shutil.rmtree, wd, True)
    await projects.materialize(db, course, wd, overlay=overlay)
    engine = await projects.engine_for(db, course)
    lset = await get_section(db, "latex")
    files = {pf.path: pf.text_content for pf in await projects.get_files(db, course.id)}
    for p, data in (overlay or {}).items():
        files[p] = data.decode("utf-8", "replace") if data is not None and p.endswith(".tex") else None
    includeonly = None if full else _chapter_for(file, files)
    body = {
        "key": f"course-{course.id}-{workdir_name}",
        "workdir": latex.rel(wd),
        "engine": engine,
        "mode": mode,
        "includeonly": includeonly,
        "priority": priority,
        "timeout": lset.timeout_s,
    }
    res = await latex.compile(body)
    status = res.get("status", "error")
    diagnostics: list[dict] = []
    if status != "superseded":
        diagnostics = texlog.parse(res.get("log", ""))
        if status != "ok" and not any(d["level"] == "error" for d in diagnostics):
            tail = (res.get("latexmk_output") or res.get("log") or "").strip().splitlines()[-3:]
            diagnostics.insert(0, {"level": "error", "file": None, "line": None, "message": " / ".join(tail) or status, "context": None})
    out = {
        "status": status,
        "engine": engine,
        "includeonly": includeonly or [],
        "seconds": res.get("seconds", 0),
        "diagnostics": diagnostics,
        "summary": texlog.summary(diagnostics),
        "pdf": res.get("pdf"),
        "log": (res.get("log") or "")[-400_000:],
        "workdir": str(wd),
    }
    if record and status != "superseded":
        b = Build(
            course_id=course.id,
            kind=kind,
            status=status,
            engine=engine,
            includeonly=includeonly or [],
            seconds=float(res.get("seconds") or 0),
            diagnostics=diagnostics,
            log=out["log"],
            pdf_path=res.get("pdf"),
        )
        db.add(b)
        await db.flush()
        out["build_id"] = b.id
        old = (
            await db.execute(
                select(Build.id)
                .where(Build.course_id == course.id, Build.kind == kind)
                .order_by(Build.id.desc())
                .offset(KEEP_BUILDS)
            )
        ).scalars().all()
        if old:
            await db.execute(delete(Build).where(Build.id.in_(old)))
        await db.commit()
    return out


def draft_pdf_path(course_id: int, workdir_name: str = "draft") -> Path:
    return projects.work_dir(course_id, workdir_name) / "main.pdf"
