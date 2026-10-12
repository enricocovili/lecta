"""Who may read and change a course (and so everything under it).

* read: the owner, admins and viewers; anybody signed in when the course is published;
* write: the owner (an editor or admin) and admins.

A course that can't be read is answered with 404, like an unknown one (an editor can't probe what others have);
one that can be read but not changed with 403.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import ColumnElement, or_, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db

from ..models import Course, User
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


def sees_everything(user: User) -> bool:
    return user.role in ("admin", "viewer")


def can_read(user: User, course: Course) -> bool:
    return course.owner_id == user.id or sees_everything(user) or course.published


def can_write(user: User, course: Course) -> bool:
    return has_role(user, "editor") and (course.owner_id == user.id or has_role(user, "admin"))


def readable(user: User) -> ColumnElement[bool]:
    """A filter on `Course` for the courses the user can read."""
    if sees_everything(user):
        return true()
    return or_(Course.owner_id == user.id, Course.published.is_(True))


def check(acc: Access, course: Course | None, *, write: bool | None = None) -> Course:
    """The course if `acc` may read it (and change it, for an unsafe request or `write=True`), else 404 or 403."""
    if course is None or not can_read(acc.user, course):
        raise HTTPException(status_code=404, detail="Not Found")
    if (acc.write if write is None else write) and not can_write(acc.user, course):
        raise forbidden()
    return course
