"""Passkeys (WebAuthn): sign in with a fingerprint, a PIN or a password manager such as Bitwarden instead of the password.

A passkey with user verification is two factors by itself, so signing in with one skips the password and the TOTP code.
The password stays as the fallback. Registration (settings) asks for a «resident» credential, so the sign-in page needs no username:
the browser or the password manager offers the passkeys it holds for the site.

The challenge of each ceremony is kept in memory (one backend process), single use, five minutes. The relying party is the site
the browser is on: its host is the RP ID and its origin the expected origin, taken from the `Origin` header, which must match the
`Host` the request came for (so a page of another site can't start a ceremony for this one).
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, base64url_to_bytes
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..db import get_db
from ..models import AdminUser, Passkey
from ..security import auth
from ..security.auth import AuthContext, require_admin

router = APIRouter()

CHALLENGE_TTL = 300
MAX_PENDING = 500
_pending: dict[str, dict[str, Any]] = {}


def _remember(purpose: str, challenge: bytes, rp_id: str, origin: str, user_id: int | None = None) -> str:
    now = time.monotonic()
    for k in [k for k, v in _pending.items() if v["exp"] < now]:
        del _pending[k]
    if len(_pending) >= MAX_PENDING:
        _pending.pop(next(iter(_pending)))
    state = secrets.token_urlsafe(24)
    _pending[state] = {"purpose": purpose, "challenge": challenge, "rp_id": rp_id, "origin": origin, "user_id": user_id, "exp": now + CHALLENGE_TTL}
    return state


def _take(state: str, purpose: str, user_id: int | None = None) -> dict[str, Any]:
    entry = _pending.pop(state, None)
    if entry is None or entry["exp"] < time.monotonic() or entry["purpose"] != purpose or entry["user_id"] != user_id:
        raise HTTPException(status_code=400, detail="La richiesta è scaduta: riprova")
    return entry


def _relying_party(request: Request) -> tuple[str, str]:
    """(RP ID, origin) of the site the browser is on."""
    origin = (request.headers.get("origin") or "").rstrip("/")
    host = urlparse(origin).hostname
    if not origin or not host or not auth._same_origin(request):
        raise HTTPException(status_code=400, detail="Origine non valida")
    return host, origin


def _credential(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or len(raw["id"]) > 1024:
        raise HTTPException(status_code=400, detail="Credenziale non valida")
    return raw


def _out(p: Passkey) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "synced": p.synced, "transports": p.transports, "created_at": p.created_at, "last_used_at": p.last_used_at}


# --------------------------------------------------------------------------- the admin's passkeys (settings)


class PasskeyAdd(BaseModel):
    state: str = Field(max_length=100)
    credential: dict[str, Any]
    name: str = Field("", max_length=100)


@router.get("/account/passkeys")
async def list_passkeys(ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(Passkey).where(Passkey.user_id == ctx.user.id).order_by(Passkey.created_at))).scalars()
    return [_out(p) for p in rows]


@router.post("/account/passkeys/options")
async def passkey_register_options(request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    rp_id, origin = _relying_party(request)
    existing = (await db.execute(select(Passkey).where(Passkey.user_id == ctx.user.id))).scalars().all()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Lecta",
        user_id=str(ctx.user.id).encode(),
        user_name=ctx.user.username,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id)) for p in existing],
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
    )
    state = _remember("register", options.challenge, rp_id, origin, ctx.user.id)
    return {"state": state, "options": json.loads(options_to_json(options))}


@router.post("/account/passkeys", status_code=201)
async def passkey_register(body: PasskeyAdd, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    pending = _take(body.state, "register", ctx.user.id)
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
    transports = body.credential.get("response", {}).get("transports")
    pk = Passkey(
        user_id=ctx.user.id, credential_id=cred_id, public_key=verified.credential_public_key, sign_count=verified.sign_count,
        name=(body.name.strip() or f"Passkey del {datetime.now(UTC):%d/%m/%Y}")[:100],
        transports=[t for t in transports if isinstance(t, str)][:6] if isinstance(transports, list) else [],
        synced=verified.credential_device_type == CredentialDeviceType.MULTI_DEVICE,
    )
    db.add(pk)
    await db.commit()
    return _out(pk)


class PasskeyRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.patch("/account/passkeys/{passkey_id}")
async def passkey_rename(passkey_id: int, body: PasskeyRename, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    pk = await db.get(Passkey, passkey_id)
    if pk is None or pk.user_id != ctx.user.id:
        raise HTTPException(status_code=404, detail="Not Found")
    pk.name = body.name.strip()
    await db.commit()
    return _out(pk)


@router.delete("/account/passkeys/{passkey_id}")
async def passkey_delete(passkey_id: int, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    pk = await db.get(Passkey, passkey_id)
    if pk is None or pk.user_id != ctx.user.id:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(pk)
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- signing in (public)


class PasskeyLogin(BaseModel):
    state: str = Field(max_length=100)
    credential: dict[str, Any]


@router.post("/auth/passkey/options")
async def passkey_login_options(request: Request) -> dict:
    auth.check_double_submit(request)
    auth.check_login_rate(request)
    rp_id, origin = _relying_party(request)
    options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)
    return {"state": _remember("login", options.challenge, rp_id, origin), "options": json.loads(options_to_json(options))}


@router.post("/auth/passkey/login")
async def passkey_login(body: PasskeyLogin, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    auth.check_double_submit(request)
    auth.check_login_rate(request)
    pending = _take(body.state, "login")
    cred = _credential(body.credential)
    pk = (await db.execute(select(Passkey).where(Passkey.credential_id == cred["id"]))).scalar_one_or_none()
    user = await db.get(AdminUser, pk.user_id) if pk else None
    fail = HTTPException(status_code=401, detail="Passkey non riconosciuta")
    if pk is None or user is None:
        auth.record_login_failure(request)
        raise fail
    try:
        verified = verify_authentication_response(
            credential=cred, expected_challenge=pending["challenge"], expected_rp_id=pending["rp_id"], expected_origin=pending["origin"],
            credential_public_key=pk.public_key, credential_current_sign_count=pk.sign_count, require_user_verification=True,
        )
    except (InvalidAuthenticationResponse, ValueError, KeyError):
        auth.record_login_failure(request)
        raise fail from None
    pk.sign_count = verified.new_sign_count
    pk.last_used_at = datetime.now(UTC)
    await db.commit()
    auth.login_limiter.reset(auth.client_ip(request))
    await auth.create_session(db, request, response, user)
    return {"ok": True, "username": user.username}
