"""Setup wizard, login/logout, account (password, TOTP 2FA)."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime

import segno
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from .. import appsecrets
from ..db import get_db
from ..models import AdminUser, Session
from ..security import auth
from ..security.auth import AuthContext, require_admin
from ..services import settings as settings_svc

router = APIRouter()


class SetupIn(BaseModel):
    setup_code: str = Field(max_length=100)
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=12, max_length=1024)
    site_title: str | None = Field(None, max_length=200)


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)
    totp: str | None = Field(None, max_length=12)


@router.get("/setup/status")
async def setup_status(db: AsyncSession = Depends(get_db)) -> dict:
    return {"needs_setup": await auth.get_admin_user(db) is None}


@router.get("/auth/csrf")
async def csrf(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    """The CSRF token for the next unsafe request: the session's own when signed in
    (re-syncing a missing or stale cookie), otherwise a pre-session double-submit one."""
    ctx = await auth.load_session(request, db)
    if ctx is not None:
        if request.cookies.get(auth.CSRF_COOKIE) != ctx.session.csrf_token:
            auth.set_session_csrf_cookie(request, response, ctx.session)
        return {"csrf": ctx.session.csrf_token}
    return {"csrf": auth.ensure_csrf_cookie(request, response)}


@router.post("/setup")
async def setup(body: SetupIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    auth.check_double_submit(request)
    auth.check_login_rate(request)
    if await auth.get_admin_user(db) is not None:
        raise auth.not_found()
    if not hmac.compare_digest(body.setup_code.strip().lower(), appsecrets.setup_code()):
        auth.record_login_failure(request)
        raise HTTPException(status_code=403, detail="Codice di configurazione errato (vedi il log del backend)")
    user = AdminUser(username=body.username, password_hash=auth.hash_password(body.password))
    db.add(user)
    try:
        await db.flush()
    except Exception as e:  # unique race: somebody else finished setup first
        await db.rollback()
        raise auth.not_found() from e
    if body.site_title:
        await settings_svc.set_section(db, "site", {"title": body.site_title}, commit=False)
    await db.commit()
    await auth.create_session(db, request, response, user)
    return {"ok": True, "username": user.username}


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    auth.check_double_submit(request)
    auth.check_login_rate(request)
    user = await auth.get_admin_user(db)
    # Always run a hash verification so timing doesn't reveal the username.
    target_hash = user.password_hash if user else auth.hash_password("timing-equaliser")
    ok = auth.verify_password(target_hash, body.password) and user is not None and user.username == body.username
    if not ok:
        auth.record_login_failure(request)
        raise HTTPException(status_code=401, detail="Nome utente o password non validi")
    assert user is not None
    if user.totp_enabled:
        if not body.totp:
            return {"ok": False, "totp_required": True}
        secret = appsecrets.decrypt(user.totp_secret_enc)
        if not secret or not auth.verify_totp(secret, body.totp):
            auth.record_login_failure(request)
            raise HTTPException(status_code=401, detail="Codice 2FA non valido")
    if auth.needs_rehash(user.password_hash):
        user.password_hash = auth.hash_password(body.password)
        await db.commit()
    auth.login_limiter.reset(auth.client_ip(request))
    await auth.create_session(db, request, response, user)
    return {"ok": True, "username": user.username}


@router.post("/auth/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    ctx = await auth.load_session(request, db)
    if ctx is not None:
        header = request.headers.get(auth.CSRF_HEADER, "")
        if not hmac.compare_digest(header, ctx.session.csrf_token):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        await auth.destroy_session(db, response, ctx.session)
    else:
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
async def me(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    ctx = await auth.load_session(request, db)
    needs_setup = await auth.get_admin_user(db) is None
    if ctx is None:
        return {"authenticated": False, "needs_setup": needs_setup}
    return {
        "authenticated": True,
        "username": ctx.user.username,
        "csrf": ctx.session.csrf_token,
        "totp_enabled": ctx.user.totp_enabled,
        "needs_setup": False,
    }


# --------------------------------------------------------------------------- account


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


@router.post("/account/password")
async def change_password(
    body: PasswordIn,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await db.get(AdminUser, ctx.user.id)
    assert user is not None
    if not auth.verify_password(user.password_hash, body.current_password):
        raise HTTPException(status_code=400, detail="La password attuale è sbagliata")
    user.password_hash = auth.hash_password(body.new_password)
    user.password_changed_at = datetime.now(UTC)
    # Log out every other session.
    await db.execute(delete(Session).where(Session.user_id == user.id, Session.id != ctx.session.id))
    await db.commit()
    return {"ok": True}


class TotpEnableIn(BaseModel):
    code: str = Field(max_length=12)


@router.post("/account/totp/setup")
async def totp_setup(ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    user = await db.get(AdminUser, ctx.user.id)
    assert user is not None
    if user.totp_enabled:
        raise HTTPException(status_code=400, detail="La verifica in due passaggi è già attiva")
    secret = auth.new_totp_secret()
    user.totp_secret_enc = appsecrets.encrypt(secret)
    await db.commit()
    uri = auth.totp_uri(secret, user.username)
    svg = segno.make(uri, error="m").svg_inline(scale=5, dark="#111", light="#fff")
    return {"secret": secret, "uri": uri, "qr_svg": svg}


@router.post("/account/totp/enable")
async def totp_enable(
    body: TotpEnableIn, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict:
    user = await db.get(AdminUser, ctx.user.id)
    assert user is not None
    secret = appsecrets.decrypt(user.totp_secret_enc)
    if not secret or not auth.verify_totp(secret, body.code):
        raise HTTPException(status_code=400, detail="Codice non valido")
    user.totp_enabled = True
    await db.commit()
    return {"ok": True}


class TotpDisableIn(BaseModel):
    password: str = Field(max_length=1024)
    code: str = Field(max_length=12)


@router.post("/account/totp/disable")
async def totp_disable(
    body: TotpDisableIn, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict:
    user = await db.get(AdminUser, ctx.user.id)
    assert user is not None
    secret = appsecrets.decrypt(user.totp_secret_enc)
    if not auth.verify_password(user.password_hash, body.password) or not secret or not auth.verify_totp(secret, body.code):
        raise HTTPException(status_code=400, detail="Password o codice errati")
    user.totp_enabled = False
    user.totp_secret_enc = None
    await db.commit()
    return {"ok": True}


@router.get("/account/sessions")
async def list_sessions(ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[dict]:
    from sqlalchemy import select

    rows = (await db.execute(select(Session).where(Session.user_id == ctx.user.id).order_by(Session.last_seen_at.desc()))).scalars()
    return [
        {
            "id": s.id[:12],
            "current": s.id == ctx.session.id,
            "created_at": s.created_at,
            "last_seen_at": s.last_seen_at,
            "ip": s.ip,
            "user_agent": s.user_agent,
        }
        for s in rows
    ]
