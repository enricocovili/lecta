"""Invite links: the only way to a new account (there are no sign-ups).

An admin creates a link with a role (and a note to remember whom it was for); the link is shown once, only the sha256 of
its token is stored, it expires after seven days and can be revoked while unused. Whoever opens it picks a username and
creates a passkey: the account has no password. Accepting verifies the passkey, consumes the invite (atomically, so two
browsers racing on the same link make one account), creates the user and the passkey in one transaction, and signs in.

A recovery link is an invite bound to an existing user (`user_id`), for someone who lost their passkey: an admin makes it
from «Utenti» (`POST /api/users/{id}/recovery`, shown once, seven days, it replaces that user's earlier pending one).
Opening it asks no username: accepting adds a passkey to that user (who must still exist and not be disabled), signs out
their other sessions (the lost device may still hold one) and signs in.

The public routes under `/api/invite/{token}` are rate limited like the sign-in: an unknown, used or expired token is a
404 and counts as a failed attempt, so tokens can't be guessed.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import generate_registration_options, options_to_json, verify_registration_response
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..db import get_db
from ..models import Invite, Passkey, Session, User
from ..security import auth
from ..security.auth import AuthContext, require_admin
from .passkeys import _credential, _relying_party, _remember, _take

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
    """A recovery link for a user who lost their passkey: whoever opens it adds a passkey to that user."""
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


class InviteOptionsIn(BaseModel):
    username: str | None = Field(None, min_length=1, max_length=64, pattern=USERNAME)  # not for a recovery link


class InviteAcceptIn(BaseModel):
    state: str = Field(max_length=100)
    credential: dict[str, Any]
    username: str | None = Field(None, min_length=1, max_length=64, pattern=USERNAME)  # not for a recovery link
    name: str = Field("", max_length=100)


def _taken() -> HTTPException:
    return HTTPException(status_code=409, detail="Questo nome utente è già in uso: scegline un altro")


def _new_username(username: str | None) -> str:
    if username is None:
        raise HTTPException(status_code=422, detail="Scegli un nome utente")
    return username


@router.post("/invite/{token}/options")
async def invite_options(token: str, body: InviteOptionsIn, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """The passkey ceremony for the new account (or the recovering user). It doesn't consume the link: accepting does."""
    auth.check_double_submit(request)
    inv, user = await _pending_invite(request, db, token)
    rp_id, origin = _relying_party(request)
    selection = AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED)
    if user is not None:
        # The same user handle as a passkey added in the settings; the passkeys the user still has are left alone.
        existing = (await db.execute(select(Passkey.credential_id).where(Passkey.user_id == user.id))).scalars().all()
        options = generate_registration_options(
            rp_id=rp_id, rp_name="Lecta", user_id=str(user.id).encode(), user_name=user.username, authenticator_selection=selection,
            exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c)) for c in existing],
        )
        state = _remember("recovery", options.challenge, rp_id, origin, user.id, bound=inv.id)
        return {"state": state, "options": json.loads(options_to_json(options))}
    username = _new_username(body.username)
    if await auth.user_by_username(db, username) is not None:
        raise _taken()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Lecta",
        user_id=secrets.token_bytes(16),  # the user doesn't exist yet: a random handle
        user_name=username,
        authenticator_selection=selection,
    )
    state = _remember("invite", options.challenge, rp_id, origin, bound=(inv.id, username))
    return {"state": state, "options": json.loads(options_to_json(options))}


def _passkey(user_id: int, cred_id: str, verified: Any, body: InviteAcceptIn) -> Passkey:
    transports = body.credential.get("response", {}).get("transports")
    return Passkey(
        user_id=user_id, credential_id=cred_id, public_key=verified.credential_public_key, sign_count=verified.sign_count,
        name=(body.name.strip() or f"Passkey del {datetime.now(UTC):%d/%m/%Y}")[:100],
        transports=[t for t in transports if isinstance(t, str)][:6] if isinstance(transports, list) else [],
        synced=verified.credential_device_type == CredentialDeviceType.MULTI_DEVICE,
    )


@router.post("/invite/{token}/accept")
async def invite_accept(token: str, body: InviteAcceptIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    auth.check_double_submit(request)
    inv, recovering = await _pending_invite(request, db, token)
    if recovering is not None:
        pending = _take(body.state, "recovery", recovering.id, bound=inv.id)
    else:
        pending = _take(body.state, "invite", bound=(inv.id, _new_username(body.username)))
    try:
        verified = verify_registration_response(
            credential=_credential(body.credential), expected_challenge=pending["challenge"], expected_rp_id=pending["rp_id"],
            expected_origin=pending["origin"], require_user_verification=True,
        )
    except (InvalidRegistrationResponse, ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"Passkey non valida: {e}") from e
    cred_id = bytes_to_base64url(verified.credential_id)
    if (await db.execute(select(Passkey.id).where(Passkey.credential_id == cred_id))).first() is not None:
        raise HTTPException(status_code=409, detail="Questa passkey è già registrata")

    # One transaction: the invite is consumed only if the account and its passkey are made, and only once (a second
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
        # Whoever holds the lost device is signed out with every other browser of the user.
        await db.execute(delete(Session).where(Session.user_id == user.id))
    else:
        user = User(username=_new_username(body.username), password_hash=None, role=inv.role)
        db.add(user)
        try:
            await db.flush()
        except IntegrityError as e:  # the username was taken in the meantime
            await db.rollback()
            raise _taken() from e
    db.add(_passkey(user.id, cred_id, verified, body))
    await db.execute(update(Invite).where(Invite.id == inv.id).values(used_by=user.id))
    try:
        await db.commit()
    except IntegrityError as e:  # the same passkey registered at the same moment
        await db.rollback()
        raise HTTPException(status_code=409, detail="Questa passkey è già registrata") from e
    auth.login_limiter.reset(auth.client_ip(request))
    await auth.create_session(db, request, response, user)
    return {"ok": True, "username": user.username}
