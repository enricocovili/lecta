"""Home page data: what the user may see (their courses and the published ones, their jobs; everything for admins
and viewers)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Chapter, ChatMessage, ChatSession, Course, Job, ProjectFile
from ..pipeline import agent
from ..security.access import Access, access, can_write, chats_visible, jobs_readable, readable
from ..services import jobs as jobs_svc
from ..services import overview
from .courses import open_notes, usernames
from .jobs import job_out

router = APIRouter(dependencies=[Depends(access)])


async def _latest_reviews(db: AsyncSession, acc: Access) -> dict[int, dict]:
    """The newest document feedback of every course, from the user's conversations (an admin's: anybody's):
    {course_id: {score, verdict, at, stale}}."""
    rows = (
        await db.execute(
            select(ChatSession.course_id, ChatMessage.review, ChatMessage.created_at, ChatMessage.id)
            .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
            .where(ChatMessage.review.is_not(None), ChatMessage.status == "done", chats_visible(acc.user))
            .order_by(ChatMessage.id)
        )
    ).all()
    return {cid: {"score": r.get("score"), "verdict": r.get("verdict"), "at": at, "message_id": mid} for cid, r, at, mid in rows}


async def _ai_activity(db: AsyncSession, acc: Access, limit: int = 12) -> list[dict]:
    """What the assistant did lately in the user's conversations: one entry per turn that answered or changed something."""
    rows = (
        await db.execute(
            select(ChatMessage, ChatSession.course_id, Course.name)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .join(Course, Course.id == ChatSession.course_id)
            .where(ChatMessage.role == "assistant", chats_visible(acc.user))
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


async def _courses(db: AsyncSession, acc: Access) -> list[dict]:
    reviews = await _latest_reviews(db, acc)
    states = await overview.course_states(db)
    counts = dict((await db.execute(select(Chapter.course_id, func.count()).group_by(Chapter.course_id))).all())
    courses = (await db.execute(select(Course).where(readable(acc.user)).order_by(Course.name))).scalars().all()
    names = await usernames(db)
    notes = await open_notes(db, acc, courses)
    return [
        {
            "id": c.id,
            "name": c.name,
            "slug": c.slug,
            "published": c.published,
            "owner_id": c.owner_id,
            "owner": names.get(c.owner_id),
            "can_write": can_write(acc.user, c),
            "updated_at": c.updated_at,
            "chapter_count": counts.get(c.id, 0),
            "open_notes": notes.get(c.id, 0),  # readers' notes waiting for the owner (0 on courses the user can't change)
            "review": ({**reviews[c.id], "stale": c.updated_at > reviews[c.id]["at"]} if c.id in reviews else None),
            "ai_running": (agent.running_turn(c.id) is not None),
            **{k: v for k, v in states.get(c.id, {}).items() if k != "updated_at"},
        }
        for c in courses
    ]


@router.get("/dashboard")
async def dashboard(acc: Access = Depends(access), db: AsyncSession = Depends(get_db)) -> dict:
    recent_docs = (
        await db.execute(
            select(ProjectFile.path, ProjectFile.updated_at, Course.id, Course.name, Chapter.id, Chapter.title)
            .join(Course, Course.id == ProjectFile.course_id)
            .outerjoin(Chapter, (Chapter.course_id == ProjectFile.course_id) & (Chapter.path == ProjectFile.path))
            .where(ProjectFile.path.like("%.tex"), readable(acc.user))
            .order_by(ProjectFile.updated_at.desc())
            .limit(10)
        )
    ).all()
    recent_courses = (await db.execute(select(Course).where(readable(acc.user)).order_by(Course.updated_at.desc()).limit(8))).scalars().all()
    running = (
        await db.execute(select(Job).where(Job.status.in_(jobs_svc.ACTIVE), jobs_readable(acc.user)).order_by(Job.id.desc()).limit(20))
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
        "activity": await overview.activity(db, jobs_readable(acc.user)),
        "ai_activity": await _ai_activity(db, acc),
        "courses": await _courses(db, acc),
    }


@router.get("/dashboard/counts")
async def dashboard_counts(acc: Access = Depends(access), db: AsyncSession = Depends(get_db)) -> dict:
    """The small numbers next to the navigation entries (polled by every workspace page)."""
    active = select(func.count()).select_from(Job).where(Job.status.in_(jobs_svc.ACTIVE), jobs_readable(acc.user))
    return {"jobs_active": (await db.execute(active)).scalar_one()}
