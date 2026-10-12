"""Who may read and change a course (and so everything under it).

* read: the owner, admins and viewers; anybody signed in when the course is published;
* write: the owner (an editor or admin) and admins.

Lessons, labs and what the imports read (sources, their pages and figures) are *private*: being published doesn't
open them, since only the course's text is published.

A job is seen by whoever started it, the owner of its course, admins and viewers; changed (cancelled, retried,
deleted) by whoever started it, the course's owner and admins.

A conversation with the assistant is its author's (admins see and continue them all); asking the assistant needs the
right to change the course.

A course that can't be read is answered with 404, like an unknown one (an editor can't probe what others have);
one that can be read but not changed with 403.

The routers depend on `access`, `private_access`, `job_access` or `chat_access`, which check the course (or job) named by the
route's path before its body is read, so no route under a course can forget it.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import ColumnElement, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import ChatMessage, ChatSession, Course, IngestFigure, IngestItem, Job, Lesson, SourceFile, Upload, User
from .auth import UNSAFE, AuthContext, forbidden, has_role, require_content


@dataclass
class Access:
    """The signed-in user, and whether the request changes something (an unsafe method)."""

    user: User
    write: bool


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


# --------------------------------------------------------------------------- the rules


def sees_everything(user: User) -> bool:
    return user.role in ("admin", "viewer")


def can_read(user: User, course: Course, *, private: bool = False) -> bool:
    return course.owner_id == user.id or sees_everything(user) or (course.published and not private)


def can_write(user: User, course: Course) -> bool:
    return has_role(user, "editor") and (course.owner_id == user.id or has_role(user, "admin"))


def readable(user: User, *, private: bool = False) -> ColumnElement[bool]:
    """A filter on `Course` for the courses the user can read (their private content, with `private`)."""
    if sees_everything(user):
        return true()
    if private:
        return Course.owner_id == user.id
    return or_(Course.owner_id == user.id, Course.published.is_(True))


def jobs_readable(user: User) -> ColumnElement[bool]:
    """A filter on `Job` for the jobs the user can see."""
    if sees_everything(user):
        return true()
    return or_(Job.user_id == user.id, Job.course_id.in_(select(Course.id).where(Course.owner_id == user.id)))


def check(acc: Access, course: Course | None, *, write: bool | None = None, private: bool = False) -> Course:
    """The course if `acc` may read it (and change it, for an unsafe request or `write=True`), else 404 or 403."""
    if course is None or not can_read(acc.user, course, private=private):
        raise _not_found()
    if (acc.write if write is None else write) and not can_write(acc.user, course):
        raise forbidden()
    return course


async def check_job(acc: Access, db: AsyncSession, job_id: int) -> Job:
    """The job if `acc` may see it (and change it, on an unsafe request), else 404 or 403."""
    job = await db.get(Job, job_id)
    if job is None:
        raise _not_found()
    course = await db.get(Course, job.course_id) if job.course_id is not None else None
    mine = job.user_id == acc.user.id or (course is not None and course.owner_id == acc.user.id)
    if not (mine or sees_everything(acc.user)):
        raise _not_found()
    if acc.write and not (mine or has_role(acc.user, "admin")):
        raise forbidden()
    return job


def chats_visible(user: User) -> ColumnElement[bool]:
    """A filter on `ChatSession` for the conversations the user sees."""
    return true() if has_role(user, "admin") else ChatSession.user_id == user.id


async def check_chat(acc: Access, db: AsyncSession, session: ChatSession | None) -> ChatSession:
    """The conversation if it is `acc`'s (or `acc` is an admin), else 404; writing to it needs the course too (403)."""
    if session is None or not (session.user_id == acc.user.id or has_role(acc.user, "admin")):
        raise _not_found()
    if acc.write:
        check(acc, await db.get(Course, session.course_id), write=True, private=session.lab_id is not None or session.lesson_id is not None)
    return session


# --------------------------------------------------------------------------- the routers' dependencies


def _param(request: Request, name: str) -> int:
    try:
        return int(request.path_params[name])
    except ValueError:
        raise _not_found() from None


def _access(request: Request, ctx: AuthContext) -> Access:
    return Access(user=ctx.user, write=request.method in UNSAFE)


async def access(request: Request, ctx: AuthContext = Depends(require_content), db: AsyncSession = Depends(get_db)) -> Access:
    """The course's text (chapters, files, draft, publishing): the path's `{course_id}`, published counting."""
    acc = _access(request, ctx)
    if "course_id" in request.path_params:
        check(acc, await db.get(Course, _param(request, "course_id")))
    return acc


async def job_access(request: Request, ctx: AuthContext = Depends(require_content), db: AsyncSession = Depends(get_db)) -> Access:
    """The jobs: the path's `{job_id}`."""
    acc = _access(request, ctx)
    if "job_id" in request.path_params:
        await check_job(acc, db, _param(request, "job_id"))
    return acc


# Path parameters of the private routes, and how each one leads to its course (None: it is the course).
_COURSE_OF = {
    "course_id": None,
    "lesson_id": lambda v: select(Lesson.course_id).where(Lesson.id == v),
    "sid": lambda v: select(Upload.target_course_id).join(SourceFile, SourceFile.upload_id == Upload.id).where(SourceFile.id == v),
    "item_id": lambda v: select(Upload.target_course_id).join(IngestItem, IngestItem.upload_id == Upload.id).where(IngestItem.id == v),
    "fig_id": lambda v: select(Job.course_id).join(IngestFigure, IngestFigure.job_id == Job.id).where(IngestFigure.id == v),
}


async def private_access(request: Request, ctx: AuthContext = Depends(require_content), db: AsyncSession = Depends(get_db)) -> Access:
    """Lessons, labs, import material: the course behind the path's lesson, source, item or figure is checked with
    `private=True`, an import's `{job_id}` as a job. Material whose course was deleted is left to admins and viewers."""
    acc = _access(request, ctx)
    if "job_id" in request.path_params:
        await check_job(acc, db, _param(request, "job_id"))
    for name, query in _COURSE_OF.items():
        if name not in request.path_params:
            continue
        value = _param(request, name)
        if query is None:
            course_id: int | None = value
        else:
            row = (await db.execute(query(value))).first()
            if row is None:
                raise _not_found()
            course_id = row[0]
        if course_id is None:
            if not sees_everything(acc.user) or (acc.write and not has_role(acc.user, "admin")):
                raise _not_found()
        else:
            check(acc, await db.get(Course, course_id), private=True)
    return acc


async def chat_access(request: Request, ctx: AuthContext = Depends(require_content), db: AsyncSession = Depends(get_db)) -> Access:
    """The assistant: the path's conversation (`{sid}`) or reply (`{rid}`) must be the user's, a `{course_id}` readable."""
    acc = _access(request, ctx)
    if "sid" in request.path_params:
        await check_chat(acc, db, await db.get(ChatSession, _param(request, "sid")))
    if "rid" in request.path_params:
        reply = await db.get(ChatMessage, _param(request, "rid"))
        await check_chat(acc, db, await db.get(ChatSession, reply.session_id) if reply is not None else None)
    if "course_id" in request.path_params:
        check(acc, await db.get(Course, _param(request, "course_id")))
    return acc
