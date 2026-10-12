from __future__ import annotations

from contextlib import asynccontextmanager

import pyotp
import pytest

from app import appsecrets
from app.db import SessionLocal
from app.models import User
from app.security.auth import login_global_limiter, login_limiter

from .conftest import ADMIN_PASSWORD, ADMIN_USER, ensure_admin, make_client


@asynccontextmanager
async def _no_admin():
    """No admin for the duration (setup is needed again): the admins are demoted, then promoted back."""
    from sqlalchemy import update

    async with SessionLocal() as db:
        ids = (await db.execute(update(User).where(User.role == "admin").values(role="editor").returning(User.id))).scalars().all()
        await db.commit()
    try:
        yield
    finally:
        async with SessionLocal() as db:
            await db.execute(update(User).where(User.id.in_(ids)).values(role="admin"))
            await db.commit()


@pytest.fixture(autouse=True)
async def _reset_limits():
    login_limiter.hits.clear()
    login_global_limiter.hits.clear()
    yield
    from sqlalchemy import delete

    async with SessionLocal() as db:  # the users a test added
        await db.execute(delete(User).where(User.username.in_(_added)))
        await db.commit()
    _added.clear()


_added: list[str] = []


async def test_setup_wizard_requires_code_and_csrf(app):
    await ensure_admin(app)
    _added.append("me")
    async with _no_admin():
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

        user = (await db.execute(select(User).where(User.username == ADMIN_USER))).scalar_one()
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


async def _add_user(username: str, role: str, password: str | None = None) -> int:
    from app.security.auth import hash_password, user_by_username

    async with SessionLocal() as db:
        if (old := await user_by_username(db, username)) is not None:
            await db.delete(old)
            await db.flush()
        user = User(username=username, role=role, password_hash=hash_password(password) if password else None)
        _added.append(username)
        db.add(user)
        await db.commit()
        return user.id


async def _password_login(c, username: str, password: str):  # noqa: ANN001
    await c.get("/api/auth/csrf")
    return await c.post("/api/auth/login", json={"username": username, "password": password})


async def test_each_user_signs_in_with_their_own_password(app):
    await ensure_admin(app)
    await _add_user("second", "editor", "second-users-password")
    async with make_client(app) as c:
        assert (await _password_login(c, "second", ADMIN_PASSWORD)).status_code == 401
        r = await _password_login(c, "second", "second-users-password")
        assert r.status_code == 200 and r.json()["username"] == "second"
        assert (await c.get("/api/auth/me")).json()["username"] == "second"


async def test_a_passkey_only_user_never_signs_in_with_a_password(app):
    await _add_user("keyonly", "viewer")
    async with make_client(app) as c:
        r = await _password_login(c, "keyonly", "")
        assert r.status_code in (401, 422)
        r = await _password_login(c, "keyonly", "anything-at-all-123")
        assert r.status_code == 401


async def test_a_disabled_user_is_signed_out_and_cannot_sign_in(app):
    from datetime import UTC, datetime

    user_id = await _add_user("leaving", "editor", "leaving-users-password")
    async with make_client(app) as c:
        assert (await _password_login(c, "leaving", "leaving-users-password")).status_code == 200
        assert (await c.get("/api/courses")).status_code == 200
        async with SessionLocal() as db:
            (await db.get(User, user_id)).disabled_at = datetime.now(UTC)
            await db.commit()
        assert (await c.get("/api/courses")).status_code == 404
        assert not (await c.get("/api/auth/me")).json()["authenticated"]
        assert (await _password_login(c, "leaving", "leaving-users-password")).status_code == 401


async def test_setup_is_needed_until_an_admin_exists(app):
    await ensure_admin(app)
    await _add_user("not-an-admin", "editor", "editors-password-123")
    async with _no_admin(), make_client(app) as c:
        assert (await c.get("/api/setup/status")).json() == {"needs_setup": True}
    async with make_client(app) as c:
        assert (await c.get("/api/setup/status")).json() == {"needs_setup": False}


async def test_cli_acts_on_the_user_it_is_given(app):
    from app import cli

    await ensure_admin(app)
    await _add_user("someone-else", "editor", "someone-elses-password")
    await cli.reset_password("a-reset-by-the-cli-1", "someone-else")
    async with make_client(app) as c:
        assert (await _password_login(c, "someone-else", "a-reset-by-the-cli-1")).status_code == 200
        assert (await _password_login(c, ADMIN_USER, ADMIN_PASSWORD)).status_code == 200
    with pytest.raises(SystemExit):
        await cli.reset_password("a-reset-by-the-cli-1", "nobody-by-this-name")
    # Without --user it means the only admin; with two admins it asks which one.
    await _add_user("second-admin", "admin", "second-admins-password")
    with pytest.raises(SystemExit, match="--user"):
        await cli.disable_2fa()
