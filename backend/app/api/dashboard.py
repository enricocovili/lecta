"""Admin homepage data."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Chapter, ChatMessage, ChatSession, Course, Job, ProjectFile
from ..security.auth import require_admin
from ..services import jobs as jobs_svc
from ..pipeline import agent
from ..services import overview
from .jobs import job_out

router = APIRouter(dependencies=[Depends(require_admin)])


async def _latest_reviews(db: AsyncSession) -> dict[int, dict]:
    """The newest document feedback of every course: {course_id: {score, verdict, at, stale}}."""
    rows = (
        await db.execute(
            select(ChatSession.course_id, ChatMessage.review, ChatMessage.created_at, ChatMessage.id)
            .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
            .where(ChatMessage.review.is_not(None), ChatMessage.status == "done")
            .order_by(ChatMessage.id)
        )
    ).all()
    return {cid: {"score": r.get("score"), "verdict": r.get("verdict"), "at": at, "message_id": mid} for cid, r, at, mid in rows}


async def _ai_activity(db: AsyncSession, limit: int = 12) -> list[dict]:
    """What the assistant did lately: one entry per turn that answered or changed something."""
    rows = (
        await db.execute(
            select(ChatMessage, ChatSession.course_id, Course.name)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .join(Course, Course.id == ChatSession.course_id)
            .where(ChatMessage.role == "assistant")
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )
    ).all()
    out = []
    for m, course_id, course_name in rows:
        change = m.change or {}
        text = agent.strip_blocks(m.content or "")
        out.append({
            "id": m.id, "session_id": m.session_id, "course_id": course_id, "course_name": course_name, "status": m.status,
            "at": m.created_at, "text": " ".join(text.split())[:200],
            "files": len(change.get("files") or []), "chapters": [c.get("title") for c in (change.get("chapters") or [])][:5],
            "undone": change.get("status") == "undone", "review_score": (m.review or {}).get("score"),
            "steps": len(m.steps or []), "running": m.status == "streaming",
        })
    return out


async def _courses(db: AsyncSession) -> list[dict]:
    reviews = await _latest_reviews(db)
    states = await overview.course_states(db)
    counts = dict((await db.execute(select(Chapter.course_id, func.count()).group_by(Chapter.course_id))).all())
    courses = (await db.execute(select(Course).order_by(Course.name))).scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "slug": c.slug,
            "published": c.published,
            "updated_at": c.updated_at,
            "chapter_count": counts.get(c.id, 0),
            "review": ({**reviews[c.id], "stale": c.updated_at > reviews[c.id]["at"]} if c.id in reviews else None),
            "ai_running": (agent.running_turn(c.id) is not None),
            **{k: v for k, v in states.get(c.id, {}).items() if k != "updated_at"},
        }
        for c in courses
    ]


@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)) -> dict:
    recent_docs = (
        await db.execute(
            select(ProjectFile.path, ProjectFile.updated_at, Course.id, Course.name, Chapter.id, Chapter.title)
            .join(Course, Course.id == ProjectFile.course_id)
            .outerjoin(Chapter, (Chapter.course_id == ProjectFile.course_id) & (Chapter.path == ProjectFile.path))
            .where(ProjectFile.path.like("%.tex"))
            .order_by(ProjectFile.updated_at.desc())
            .limit(10)
        )
    ).all()
    recent_courses = (await db.execute(select(Course).order_by(Course.updated_at.desc()).limit(8))).scalars().all()
    running = (
        await db.execute(select(Job).where(Job.status.in_(jobs_svc.ACTIVE)).order_by(Job.id.desc()).limit(20))
    ).scalars().all()
    return {
        "recent_documents": [
            {
                "path": p,
                "updated_at": u,
                "course_id": cid,
                "course_name": cname,
                "chapter_id": chid,
                "chapter_title": chtitle,
            }
            for p, u, cid, cname, chid, chtitle in recent_docs
        ],
        "recent_courses": [
            {"id": c.id, "name": c.name, "slug": c.slug, "updated_at": c.updated_at, "published": c.published}
            for c in recent_courses
        ],
        "jobs": [job_out(j) for j in running],
        "activity": await overview.activity(db),
        "ai_activity": await _ai_activity(db),
        "courses": await _courses(db),
    }


@router.get("/dashboard/counts")
async def dashboard_counts(db: AsyncSession = Depends(get_db)) -> dict:
    """The small numbers next to the navigation entries (polled by every workspace page)."""
    return {
        "jobs_active": (await db.execute(select(func.count()).select_from(Job).where(Job.status.in_(jobs_svc.ACTIVE)))).scalar_one(),
    }
