"""Workspace overview: per-course state (done / working / error) and the activity feed.

Used by the admin Home, the sidebar tree and the concept map. Everything is
derived from existing rows (jobs, builds, source links); nothing is
stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Build, Chapter, Course, Job, SourceFile, SourceLink, Upload
from . import jobs as jobs_svc

# Internal/maintenance jobs that don't belong in the activity feed.
HIDDEN_KINDS = ("embeddings.benchmark", "index.rebuild", "demo.sleep")


def _first_line(text: str | None, limit: int = 240) -> str | None:
    if not text:
        return None
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[:limit] or None


def _ts(dt: datetime | None) -> float:
    return dt.timestamp() if dt else 0.0


async def course_states(db: AsyncSession) -> dict[int, dict[str, Any]]:
    """State of every course: {status: ok|working|error, reason, active_job, last_build, sources}."""
    courses = (await db.execute(select(Course.id, Course.updated_at))).all()
    out: dict[int, dict[str, Any]] = {
        cid: {"status": "ok", "reason": None, "active_job": None, "last_build": None, "sources": 0, "updated_at": upd}
        for cid, upd in courses
    }
    if not out:
        return out

    # Latest non-superseded draft/publish build per course.
    latest_build = (
        select(Build.course_id, func.max(Build.id).label("bid"))
        .where(Build.kind.in_(("draft", "publish")), Build.status != "superseded")
        .group_by(Build.course_id)
        .subquery()
    )
    for b in (await db.execute(select(Build).join(latest_build, latest_build.c.bid == Build.id))).scalars():
        errors = [d for d in (b.diagnostics or []) if isinstance(d, dict) and d.get("level") == "error"]
        first = errors[0] if errors else None
        out[b.course_id]["last_build"] = {
            "id": b.id,
            "status": b.status,
            "at": b.created_at,
            "errors": len(errors),
            "first_error": (
                {"file": first.get("file"), "line": first.get("line"), "message": (first.get("message") or "")[:200]} if first else None
            ),
        }

    # Name the chapter a compile error points at ("Memoria virtuale", line 212).
    wanted = {(cid, st["last_build"]["first_error"]["file"]) for cid, st in out.items() if st["last_build"] and st["last_build"]["first_error"] and st["last_build"]["first_error"]["file"]}
    if wanted:
        rows = await db.execute(select(Chapter.course_id, Chapter.path, Chapter.id, Chapter.title).where(Chapter.course_id.in_([c for c, _ in wanted])))
        for cid, path, ch_id, title in rows.all():
            if (cid, path) in wanted:
                fe = out[cid]["last_build"]["first_error"]
                fe["chapter_id"], fe["chapter_title"] = ch_id, title

    # Active top-level jobs (newest first) and the latest finished one per course.
    jobs = (
        await db.execute(
            select(Job)
            .where(Job.course_id.is_not(None), Job.parent_id.is_(None), Job.kind.not_in(HIDDEN_KINDS))
            .order_by(Job.id.desc())
            .limit(400)
        )
    ).scalars().all()
    last_finished: dict[int, Job] = {}
    for j in jobs:
        st = out.get(j.course_id)  # type: ignore[arg-type]
        if st is None:
            continue
        if j.status in jobs_svc.ACTIVE:
            if st["active_job"] is None:
                st["active_job"] = {"id": j.id, "kind": j.kind, "title": j.title, "status": j.status, "progress": j.progress, "progress_text": j.progress_text}
        elif j.course_id not in last_finished and j.status in ("succeeded", "failed"):
            last_finished[j.course_id] = j  # type: ignore[index]

    for cid, n in (await db.execute(select(SourceLink.course_id, func.count(func.distinct(SourceLink.source_file_id))).group_by(SourceLink.course_id))).all():
        if cid in out:
            out[cid]["sources"] = n

    for cid, st in out.items():
        build = st["last_build"]
        failed = last_finished.get(cid)
        build_at = _ts(build["at"]) if build else 0.0
        if build and build["status"] in ("error", "timeout"):
            st["status"], st["reason"] = "error", "compile"
        elif failed is not None and failed.status == "failed" and _ts(failed.finished_at or failed.updated_at) > build_at:
            st["status"], st["reason"] = "error", "job"
            st["failed_job"] = {"id": failed.id, "title": failed.title, "error": _first_line(failed.error)}
        elif st["active_job"]:
            st["status"] = "working"
    return out


async def activity(db: AsyncSession, limit: int = 30) -> list[dict[str, Any]]:
    """Recent top-level jobs with what the Home needs to render them."""
    rows = (
        await db.execute(
            select(Job, Course.name)
            .outerjoin(Course, Course.id == Job.course_id)
            .where(Job.parent_id.is_(None), Job.kind.not_in(HIDDEN_KINDS))
            .order_by(Job.id.desc())
            .limit(limit)
        )
    ).all()
    ids = [j.id for j, _ in rows]
    uploads: dict[int, dict[str, Any]] = {}
    if ids:
        ups = (await db.execute(select(Upload).where(Upload.job_id.in_(ids)))).scalars().all()
        names: dict[int, list[tuple[str, str]]] = {}
        if ups:
            for up_id, name, kind in (
                await db.execute(
                    select(SourceFile.upload_id, SourceFile.name, SourceFile.kind)
                    .where(SourceFile.upload_id.in_([u.id for u in ups]), SourceFile.parent_id.is_(None))
                    .order_by(SourceFile.id)
                )
            ).all():
                names.setdefault(up_id, []).append((name, kind))
        for u in ups:
            files = names.get(u.id, [])
            uploads[u.job_id] = {  # type: ignore[index]
                "via": u.via,
                "file_count": u.file_count,
                "names": [n for n, _ in files[:3]],
                "images": sum(1 for _, k in files if k == "image"),
            }
    out = []
    for j, course_name in rows:
        result = j.result if isinstance(j.result, dict) else {}
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else None
        out.append(
            {
                "id": j.id,
                "kind": j.kind,
                "title": j.title,
                "status": j.status,
                "progress": j.progress,
                "progress_text": j.progress_text,
                "error": _first_line(j.error),
                "course_id": j.course_id,
                "course_name": course_name,
                "created_at": j.created_at,
                "updated_at": j.updated_at,
                "finished_at": j.finished_at,
                "upload": uploads.get(j.id),
                "summary": summary,
                "results": [_result_out(g) for g in (result.get("groups") or []) if isinstance(g, dict)],
            }
        )
    return out


def _result_out(g: dict[str, Any]) -> dict[str, Any]:
    """Where an import put one group of material (for the Home and the job page)."""
    keys = ("type", "title", "group", "course_id", "course_name", "chapter_id", "chapter_title", "path", "inbox_id", "compile")
    return {k: g.get(k) for k in keys}
