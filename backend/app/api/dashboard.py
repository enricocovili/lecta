"""Home page data: the courses the user may see (their own and the published ones; everything for admins and viewers),
each with its state."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Chapter, Course, Job
from ..pipeline import agent
from ..security.access import Access, access, can_write, jobs_readable, readable
from ..services import jobs as jobs_svc
from ..services import overview
from .courses import open_notes, usernames

router = APIRouter(dependencies=[Depends(access)])


async def _courses(db: AsyncSession, acc: Access) -> list[dict]:
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
            "ai_running": (agent.running_turn(c.id) is not None),
            **{k: v for k, v in states.get(c.id, {}).items() if k != "updated_at"},
        }
        for c in courses
    ]


@router.get("/dashboard")
async def dashboard(acc: Access = Depends(access), db: AsyncSession = Depends(get_db)) -> dict:
    """The courses of the home page, each with its state."""
    return {"courses": await _courses(db, acc)}


@router.get("/dashboard/counts")
async def dashboard_counts(acc: Access = Depends(access), db: AsyncSession = Depends(get_db)) -> dict:
    """The small numbers next to the navigation entries (polled by every workspace page)."""
    active = select(func.count()).select_from(Job).where(Job.status.in_(jobs_svc.ACTIVE), jobs_readable(acc.user))
    return {"jobs_active": (await db.execute(active)).scalar_one()}
