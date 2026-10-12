"""The users, for the admins: list, change role, disable and re-enable, sign out everywhere, delete.

Users are disabled rather than deleted (they can't sign in, their sessions go, their content stays); deleting is allowed
only for a user who owns no courses (their conversations go with them, their jobs and invites forget them). The last
active admin can't be demoted, disabled or deleted: the check runs under a transaction-level advisory lock, so two
admins demoting each other at the same moment can't both pass. An admin may demote or disable themselves when another
active admin remains, but not delete their own account.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Course, Passkey, Session, User
from ..security import auth
from ..security.auth import AuthContext, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

# Serialises the changes that could leave Lecta without an active admin (any constant distinct from migrate.LOCK_ID).
ADMINS_LOCK = 0x1EC7A_AD31


async def _users(db: AsyncSession, me: int, user_id: int | None = None) -> list[dict]:
    seen = select(Session.user_id, func.max(Session.last_seen_at).label("v")).group_by(Session.user_id).subquery()
    keys = select(Passkey.user_id, func.count().label("v")).group_by(Passkey.user_id).subquery()
    owned = select(Course.owner_id, func.count().label("v")).group_by(Course.owner_id).subquery()
    q = (
        select(User, seen.c.v, keys.c.v, owned.c.v)
        .outerjoin(seen, seen.c.user_id == User.id)
        .outerjoin(keys, keys.c.user_id == User.id)
        .outerjoin(owned, owned.c.owner_id == User.id)
        .order_by(User.created_at, User.id)
    )
    if user_id is not None:
        q = q.where(User.id == user_id)
    return [
        {
            "id": u.id, "username": u.username, "role": u.role, "created_at": u.created_at, "last_seen_at": last_seen,
            "passkeys": passkeys or 0, "has_password": u.password_hash is not None, "disabled_at": u.disabled_at,
            "courses": courses or 0, "you": u.id == me,
        }
        for u, last_seen, passkeys, courses in (await db.execute(q)).all()
    ]


async def _locked_user(db: AsyncSession, user_id: int) -> User:
    """The user, read after taking the admins' lock (held until the transaction ends)."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADMINS_LOCK})
    user = (await db.execute(select(User).where(User.id == user_id).execution_options(populate_existing=True))).scalar_one_or_none()
    if user is None:
        raise auth.not_found()
    return user


async def _keep_an_admin(db: AsyncSession, user: User) -> None:
    """409 if `user` is the last active admin (call it before demoting, disabling or deleting them, under the lock)."""
    if user.role != "admin" or user.disabled_at is not None:
        return
    others = await db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.disabled_at.is_(None), User.id != user.id))
    if not others:
        raise HTTPException(status_code=409, detail="È l'ultimo amministratore attivo: prima nomina amministratore qualcun altro")


async def _done(db: AsyncSession, ctx: AuthContext, user_id: int) -> dict:
    await db.commit()
    return (await _users(db, ctx.user.id, user_id))[0]


@router.get("/users")
async def list_users(ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await _users(db, ctx.user.id)


class UserPatch(BaseModel):
    role: Literal["viewer", "editor", "admin"] | None = None


@router.patch("/users/{user_id}")
async def update_user(user_id: int, body: UserPatch, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    user = await _locked_user(db, user_id)
    if body.role is not None and body.role != user.role:
        if body.role != "admin":
            await _keep_an_admin(db, user)
        user.role = body.role  # takes effect on the user's next request: the role is read with the session
    return await _done(db, ctx, user.id)


@router.post("/users/{user_id}/disable")
async def disable_user(user_id: int, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    user = await _locked_user(db, user_id)
    await _keep_an_admin(db, user)
    if user.disabled_at is None:
        user.disabled_at = datetime.now(UTC)
    await db.execute(delete(Session).where(Session.user_id == user.id))
    return await _done(db, ctx, user.id)


@router.post("/users/{user_id}/enable")
async def enable_user(user_id: int, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    user = await _locked_user(db, user_id)
    user.disabled_at = None
    return await _done(db, ctx, user.id)


@router.post("/users/{user_id}/logout")
async def logout_user(user_id: int, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    """Signs the user out of every browser (the admin's own current session too, when it is theirs)."""
    if await db.get(User, user_id) is None:
        raise auth.not_found()
    await db.execute(delete(Session).where(Session.user_id == user_id))
    return await _done(db, ctx, user_id)


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    user = await _locked_user(db, user_id)
    if user.id == ctx.user.id:
        raise HTTPException(status_code=409, detail="Non puoi eliminare il tuo account: chiedilo a un altro amministratore")
    await _keep_an_admin(db, user)
    if await db.scalar(select(func.count()).select_from(Course).where(Course.owner_id == user.id)):
        raise HTTPException(status_code=409, detail="Ha delle materie: disattivalo invece di eliminarlo")
    await db.delete(user)  # passkeys, sessions and conversations go by cascade; jobs and invites keep a NULL user
    await db.commit()
    return {"ok": True}
