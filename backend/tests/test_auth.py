from __future__ import annotations

import pyotp
import pytest

from app import appsecrets
from app.db import SessionLocal
from app.models import AdminUser
from app.security.auth import login_global_limiter, login_limiter

from .conftest import ADMIN_PASSWORD, ADMIN_USER, ensure_admin, make_client


async def _wipe_admin() -> None:
    from sqlalchemy import delete

    async with SessionLocal() as db:
        await db.execute(delete(AdminUser))
        await db.commit()


@pytest.fixture(autouse=True)
def _reset_limits():
    login_limiter.hits.clear()
    login_global_limiter.hits.clear()


async def test_setup_wizard_requires_code_and_csrf(app):
    await _wipe_admin()
    async with make_client(app) as c:
        assert (await c.get("/api/setup/status")).json() == {"needs_setup": True}
        body = {"setup_code": appsecrets.setup_code(), "username": "me", "password": "a-long-password-123"}
        # No CSRF cookie/header -> refused.
        r = await c.post("/api/setup", json=body, headers={"x-csrf-token": ""})
        assert r.status_code == 403
        await c.get("/api/auth/csrf")
        r = await c.post("/api/setup", json={**body, "setup_code": "wrong"})
        assert r.status_code == 403
        r = await c.post("/api/setup", json={**body, "password": "short"})
        assert r.status_code == 422
        r = await c.post("/api/setup", json=body)
        assert r.status_code == 200, r.text
        me = (await c.get("/api/auth/me")).json()
        assert me["authenticated"] and me["username"] == "me"
        # Session cookie is httpOnly + SameSite=Lax.
        set_cookie = r.headers.get_list("set-cookie")
        session_cookie = next(h for h in set_cookie if h.startswith("lecta_session="))
        assert "HttpOnly" in session_cookie and "samesite=lax" in session_cookie.lower()
    # Setup can't run twice (and looks like an unknown route afterwards).
    async with make_client(app) as c:
        await c.get("/api/auth/csrf")
        r = await c.post("/api/setup", json=body)
        assert r.status_code == 404
    await _wipe_admin()
    await ensure_admin(app)


async def test_login_logout_and_rate_limit(app):
    await ensure_admin(app)
    async with make_client(app) as c:
        await c.get("/api/auth/csrf")
        r = await c.post("/api/auth/login", json={"username": ADMIN_USER, "password": "nope"})
        assert r.status_code == 401
        r = await c.post("/api/auth/login", json={"username": "someone", "password": ADMIN_PASSWORD})
        assert r.status_code == 401
        r = await c.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        me = (await c.get("/api/auth/me")).json()
        assert me["authenticated"]
        c.csrf = me["csrf"]
        # Private endpoint works with the session...
        assert (await c.get("/api/courses")).status_code == 200
        # ...unsafe methods need the CSRF token.
        r = await c.post("/api/courses", json={"name": "X"}, headers={"x-csrf-token": "bad"})
        assert r.status_code == 403
        r = await c.post("/api/auth/logout")
        assert r.status_code == 200
        assert (await c.get("/api/courses")).status_code == 404

    async with make_client(app) as c:
        await c.get("/api/auth/csrf")
        for _ in range(10):
            await c.post("/api/auth/login", json={"username": ADMIN_USER, "password": "wrong"})
        r = await c.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
        assert r.status_code == 429


async def test_totp_flow(app, admin):
    r = await admin.post("/api/account/totp/setup")
    assert r.status_code == 200
    secret = r.json()["secret"]
    assert "<svg" in r.json()["qr_svg"]
    assert (await admin.post("/api/account/totp/enable", json={"code": "000000"})).status_code == 400
    r = await admin.post("/api/account/totp/enable", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200

    async with make_client(app) as c:
        await c.get("/api/auth/csrf")
        r = await c.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
        assert r.json() == {"ok": False, "totp_required": True}
        assert not (await c.get("/api/auth/me")).json()["authenticated"]
        r = await c.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "totp": "123456"})
        assert r.status_code == 401
        r = await c.post(
            "/api/auth/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "totp": pyotp.TOTP(secret).now()},
        )
        assert r.status_code == 200 and r.json()["ok"]

    r = await admin.post(
        "/api/account/totp/disable", json={"password": ADMIN_PASSWORD, "code": pyotp.TOTP(secret).now()}
    )
    assert r.status_code == 200


async def test_password_hash_is_argon2(app):
    await ensure_admin(app)
    async with SessionLocal() as db:
        from sqlalchemy import select

        user = (await db.execute(select(AdminUser))).scalar_one()
        assert user.password_hash.startswith("$argon2id$")


async def test_secure_cookie_behind_https(app, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "cookie_secure", "auto")
    await ensure_admin(app)
    async with make_client(app) as c:
        await c.get("/api/auth/csrf")
        r = await c.post(
            "/api/auth/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
            headers={"x-forwarded-proto": "https"},
        )
        # ASGITransport doesn't run uvicorn's proxy-headers middleware; emulate the scheme instead.
        assert r.status_code == 200
    from starlette.requests import Request

    from app.security.auth import is_https

    req = Request({"type": "http", "scheme": "https", "headers": [], "path": "/"})
    assert is_https(req)
    req = Request({"type": "http", "scheme": "http", "headers": [], "path": "/"})
    assert not is_https(req)


async def test_cli_password_reset(app):
    from app import cli

    await ensure_admin(app)
    await cli.reset_password("a-brand-new-password")
    async with make_client(app) as c:
        await c.get("/api/auth/csrf")
        r = await c.post("/api/auth/login", json={"username": ADMIN_USER, "password": "a-brand-new-password"})
        assert r.status_code == 200
    await cli.reset_password(ADMIN_PASSWORD)
    await cli.disable_2fa()


async def test_csrf_cookie_resyncs_to_the_session(app):
    """A browser that kept the session cookie but lost (or has a stale) CSRF cookie recovers."""
    login_limiter.hits.clear()
    login_global_limiter.hits.clear()
    await ensure_admin(app)
    async with make_client(app) as c:
        await c.get("/api/auth/csrf")
        r = await c.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        assert "max-age" in r.headers.get("set-cookie", "").lower().split("lecta_csrf=", 1)[1]
        session_token = (await c.get("/api/auth/me")).json()["csrf"]
        r = await c.get("/api/auth/csrf", headers={"cookie": f"lecta_session={c.cookies['lecta_session']}; lecta_csrf={'x' * 43}"})
        assert r.json()["csrf"] == session_token  # not the stale pre-session token
        assert f"lecta_csrf={session_token}" in r.headers.get("set-cookie", "")
        r = await c.post("/api/courses", json={"name": "Resync"}, headers={"x-csrf-token": r.json()["csrf"]})
        assert r.status_code in (200, 201), r.text
