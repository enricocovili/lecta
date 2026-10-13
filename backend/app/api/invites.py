"""Invite links: the only way to a new account (there are no sign-ups).

An admin creates a link with a role (and a note to remember whom it was for); the link is shown once, only the sha256 of
its token is stored, it expires after seven days and can be revoked while unused. Whoever opens it picks a username and a
password. Accepting consumes the invite (atomically, so two browsers racing on the same link make one account), creates
the user and signs in. A passkey is optional: the invite page offers to make one right after, through the signed-in
user's own passkey routes.

A recovery link is an invite bound to an existing user (`user_id`), for someone who lost their password or their passkey:
an admin makes it from «Utenti» (`POST /api/users/{id}/recovery`, shown once, seven days, it replaces that user's earlier
pending one). Opening it asks no username: accepting sets a new password for that user (who must still exist and not be
disabled), signs out their other sessions (a lost device may still hold one) and signs in; their passkeys stay.

The public routes under `/api/invite/{token}` are rate limited like the sign-in: an unknown, used or expired token is a
404 and counts as a failed attempt, so tokens can't be guessed.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Invite, Session, User
from ..security import auth
from ..security.auth import AuthContext, require_admin

admin_router = APIRouter(dependencies=[Depends(require_admin)])
router = APIRouter()

INVITE_TTL = timedelta(days=7)
USERNAME = r"^[A-Za-z0-9._@-]+$"


def _out(inv: Invite, username: str | None = None) -> dict[str, Any]:
    """An invite for the admins; a recovery link (`recovery`) carries the username of the user it is for."""
    return {
        "id": inv.id, "role": inv.role, "note": inv.note, "user_id": inv.user_id, "recovery": inv.user_id is not None, "username": username,
        "created_at": inv.created_at, "expires_at": inv.expires_at,
    }


async def _new_link(db: AsyncSession, ctx: AuthContext, role: str, note: str | None = None, user: User | None = None) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    inv = Invite(
        token_hash=auth._token_id(token), role=role, note=note, user_id=user.id if user else None, created_by=ctx.user.id,
        expires_at=datetime.now(UTC) + INVITE_TTL,
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    # The token is shown this once: only its hash stays.
    return {**_out(inv, user.username if user else None), "token": token, "path": f"/invito/{token}"}


# --------------------------------------------------------------------------- the admin's invites


class InviteIn(BaseModel):
    role: Literal["viewer", "editor", "admin"]
    note: str | None = Field(None, max_length=500)


@admin_router.post("/invites", status_code=201)
async def create_invite(body: InviteIn, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    return await _new_link(db, ctx, body.role, (body.note or "").strip() or None)


@admin_router.post("/users/{user_id}/recovery", status_code=201)
async def create_recovery(user_id: int, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    """A recovery link for a user who lost their password or passkey: whoever opens it sets a new password for that user."""
    user = await db.get(User, user_id)
    if user is None:
        raise auth.not_found()
    if user.disabled_at is not None:
        raise HTTPException(status_code=409, detail="È disattivato: riattivalo prima di mandargli un link di recupero")
    # One pending link per user: a new one replaces the one sent before.
    await db.execute(delete(Invite).where(Invite.user_id == user.id, Invite.used_at.is_(None)))
    return await _new_link(db, ctx, user.role, user=user)


@admin_router.get("/invites")
async def list_invites(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(
        select(Invite, User.username).outerjoin(User, User.id == Invite.user_id)
        .where(Invite.used_at.is_(None), Invite.expires_at > datetime.now(UTC)).order_by(Invite.created_at.desc())
    )).all()
    return [_out(i, username) for i, username in rows]


@admin_router.delete("/invites/{invite_id}")
async def revoke_invite(invite_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    inv = await db.get(Invite, invite_id)
    if inv is None or inv.used_at is not None:
        raise auth.not_found()
    await db.delete(inv)
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- opening a link (public)


async def _pending_invite(request: Request, db: AsyncSession, token: str) -> tuple[Invite, User | None]:
    """The invite of this token if it can still be used, with the user of a recovery link (who must exist and not be
    disabled); otherwise 404, counted as a failed sign-in."""
    auth.check_login_rate(request)
    inv = (await db.execute(select(Invite).where(Invite.token_hash == auth._token_id(token)))).scalar_one_or_none()
    user = await db.get(User, inv.user_id) if inv is not None and inv.user_id is not None else None
    if (
        inv is None or inv.used_at is not None or inv.expires_at <= datetime.now(UTC)
        or (inv.user_id is not None and (user is None or user.disabled_at is not None))
    ):
        auth.record_login_failure(request)
        raise auth.not_found()
    return inv, user


@router.get("/invite/{token}")
async def invite_info(token: str, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    inv, user = await _pending_invite(request, db, token)
    if user is not None:
        return {"role": user.role, "note": None, "expires_at": inv.expires_at, "recovery": True, "username": user.username}
    return {"role": inv.role, "note": inv.note, "expires_at": inv.expires_at, "recovery": False, "username": None}


class InviteAcceptIn(BaseModel):
    username: str | None = Field(None, min_length=1, max_length=64, pattern=USERNAME)  # not for a recovery link
    password: str = Field(min_length=12, max_length=1024)


def _taken() -> HTTPException:
    return HTTPException(status_code=409, detail="Questo nome utente è già in uso: scegline un altro")


@router.post("/invite/{token}/accept")
async def invite_accept(token: str, body: InviteAcceptIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    auth.check_double_submit(request)
    inv, recovering = await _pending_invite(request, db, token)
    if recovering is None and body.username is None:
        raise HTTPException(status_code=422, detail="Scegli un nome utente")
    if recovering is None and await auth.user_by_username(db, body.username) is not None:
        raise _taken()
    password_hash = auth.hash_password(body.password)

    # One transaction: the invite is consumed only if the account is made (or the password set), and only once (a second
    # accept waits on the row lock, then finds it used).
    consumed = (await db.execute(
        update(Invite).where(Invite.id == inv.id, Invite.used_at.is_(None), Invite.expires_at > datetime.now(UTC))
        .values(used_at=datetime.now(UTC)).returning(Invite.id)
    )).first()
    if consumed is None:
        await db.rollback()
        raise auth.not_found()
    if recovering is not None:
        # Still there and enabled now (the row lock keeps a disable from slipping in before the commit).
        user = (await db.execute(
            select(User).where(User.id == recovering.id).with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if user is None or user.disabled_at is not None:
            await db.rollback()
            raise auth.not_found()
        user.password_hash = password_hash
        user.password_changed_at = datetime.now(UTC)
        # Whoever holds the lost device is signed out with every other browser of the user.
        await db.execute(delete(Session).where(Session.user_id == user.id))
    else:
        user = User(username=body.username, password_hash=password_hash, role=inv.role)
        db.add(user)
        try:
            await db.flush()
        except IntegrityError as e:  # the username was taken in the meantime
            await db.rollback()
            raise _taken() from e
    await db.execute(update(Invite).where(Invite.id == inv.id).values(used_by=user.id))
    await db.commit()
    auth.login_limiter.reset(auth.client_ip(request))
    await auth.create_session(db, request, response, user)
    return {"ok": True, "username": user.username}
