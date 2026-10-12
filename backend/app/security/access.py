"""Who may read and change a course (and so everything under it).

* read: the owner, admins and viewers; anybody signed in when the course is published;
* write: the owner (an editor or admin) and admins.

Lessons, labs and what the imports read (sources, their pages and figures) are *private*: being published doesn't
open them, since only the course's text is published.

A course that can't be read is answered with 404, like an unknown one (an editor can't probe what others have);
one that can be read but not changed with 403.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import ColumnElement, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db

from ..models import Course, IngestFigure, IngestItem, Job, Lesson, SourceFile, Upload, User
from .auth import UNSAFE, AuthContext, forbidden, has_role, require_content


@dataclass
class Access:
    """The signed-in user, and whether the request changes something (an unsafe method)."""

    user: User
    write: bool


async def access(request: Request, ctx: AuthContext = Depends(require_content), db: AsyncSession = Depends(get_db)) -> Access:
    """The routers of a course's content depend on this: a route with `{course_id}` in its path is checked here,
    before its body is read, so no route under a course can forget it (the endpoint gets the course again with
    `load_course`)."""
    acc = Access(user=ctx.user, write=request.method in UNSAFE)
    if "course_id" in request.path_params:
        try:
            course_id = int(request.path_params["course_id"])
        except ValueError:
            raise HTTPException(status_code=404, detail="Not Found") from None
        check(acc, await db.get(Course, course_id))
    return acc


# Path parameters of the private routes, and how each one leads to its course.
_COURSE_OF = {
    "course_id": None,
    "lesson_id": lambda v: select(Lesson.course_id).where(Lesson.id == v),
    "sid": lambda v: select(Upload.target_course_id).join(SourceFile, SourceFile.upload_id == Upload.id).where(SourceFile.id == v),
    "item_id": lambda v: select(Upload.target_course_id).join(IngestItem, IngestItem.upload_id == Upload.id).where(IngestItem.id == v),
    "fig_id": lambda v: select(Job.course_id).join(IngestFigure, IngestFigure.job_id == Job.id).where(IngestFigure.id == v),
    "job_id": lambda v: select(Job.course_id).where(Job.id == v),
}


async def private_access(request: Request, ctx: AuthContext = Depends(require_content), db: AsyncSession = Depends(get_db)) -> Access:
    """`access` for the private routers (lessons, labs, import material): the course behind the path's lesson, lab,
    source, item, figure or import is checked with `private=True`. Material whose course was deleted is left to
    admins and viewers."""
    acc = Access(user=ctx.user, write=request.method in UNSAFE)
    for name, query in _COURSE_OF.items():
        if name not in request.path_params:
            continue
        try:
            value = int(request.path_params[name])
        except ValueError:
            raise HTTPException(status_code=404, detail="Not Found") from None
        if query is None:
            course_id: int | None = value
        else:
            row = (await db.execute(query(value))).first()
            if row is None:
                raise HTTPException(status_code=404, detail="Not Found")
            course_id = row[0]
        if course_id is None:
            if not sees_everything(acc.user) or (acc.write and not has_role(acc.user, "admin")):
                raise HTTPException(status_code=404, detail="Not Found")
        else:
            check(acc, await db.get(Course, course_id), private=True)
    return acc


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


def check(acc: Access, course: Course | None, *, write: bool | None = None, private: bool = False) -> Course:
    """The course if `acc` may read it (and change it, for an unsafe request or `write=True`), else 404 or 403."""
    if course is None or not can_read(acc.user, course, private=private):
        raise HTTPException(status_code=404, detail="Not Found")
    if (acc.write if write is None else write) and not can_write(acc.user, course):
        raise forbidden()
    return course
