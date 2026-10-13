"""Invite links: an admin creates one, whoever opens it creates an account with a username and a password, and may
add a passkey (a software authenticator stands in for the browser); a recovery link sets a new password."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.models import Invite, Passkey, User
from app.security.auth import login_global_limiter, login_limiter

from .conftest import make_client
from .test_passkeys import HEADERS, Authenticator, sign_in

PASSWORD = "una-password-lunga"


@pytest.fixture(autouse=True)
async def _clean():
    async def wipe() -> None:
        async with SessionLocal() as db:
            await db.execute(delete(Invite))
            await db.execute(delete(User).where(User.username.like("inv-%")))
            await db.commit()

    login_limiter.hits.clear()
    login_global_limiter.hits.clear()
    await wipe()
    yield
    await wipe()


async def invite(admin, role: str = "editor", note: str | None = "per Anna") -> dict:
    r = await admin.post("/api/invites", json={"role": role, "note": note})
    assert r.status_code == 201, r.text
    return r.json()


async def browser(app):
    c = make_client(app)
    await c.get("/api/auth/csrf")
    return c


async def accept(c, token: str, username: str | None = None, password: str = PASSWORD):
    """Accepts the link (no username for a recovery link): the accept response."""
    return await c.post(f"/api/invite/{token}/accept", headers=HEADERS, json={"username": username, "password": password})


async def login(app, username: str, password: str = PASSWORD) -> int:
    c = await browser(app)
    r = await c.post("/api/auth/login", json={"username": username, "password": password})
    await c.aclose()
    return r.status_code


async def add_passkey(c, key: Authenticator, name: str = ""):
    start = (await c.post("/api/account/passkeys/options", headers=HEADERS)).json()
    return await c.post("/api/account/passkeys", headers=HEADERS, json={"state": start["state"], "credential": key.create(start["options"]), "name": name})


async def user(username: str) -> User | None:
    async with SessionLocal() as db:
        return (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()


async def test_an_invite_makes_an_account_with_a_password_and_its_role(app, admin, anon):
    inv = await invite(admin)
    token = inv["token"]
    assert inv["path"] == f"/invito/{token}" and inv["role"] == "editor" and inv["note"] == "per Anna"
    # Only the hash is stored; the list never shows the token.
    async with SessionLocal() as db:
        assert token not in [i.token_hash for i in (await db.execute(select(Invite))).scalars()]
    listed = (await admin.get("/api/invites")).json()
    assert [i["id"] for i in listed] == [inv["id"]] and "token" not in listed[0]

    # Anyone with the link sees what it is for.
    info = await anon.get(f"/api/invite/{token}")
    assert info.status_code == 200 and info.json()["role"] == "editor" and info.json()["note"] == "per Anna"
    assert info.json()["recovery"] is False

    c = await browser(app)
    r = await accept(c, token, "inv-anna")
    assert r.status_code == 200 and r.json() == {"ok": True, "username": "inv-anna"}, r.text

    # Signed in as the new user, who has the invite's role and a password, and no passkey yet.
    me = (await c.get("/api/auth/me")).json()
    assert me["authenticated"] and me["username"] == "inv-anna" and me["role"] == "editor" and me["has_password"] is True
    u = await user("inv-anna")
    assert u is not None and u.role == "editor" and u.password_hash is not None
    c.csrf = me["csrf"]
    assert (await c.get("/api/account/passkeys")).json() == []
    assert (await c.get("/api/invites")).status_code == 403
    # The passkey is optional: the page makes one right after, with the new session.
    key = Authenticator()
    assert (await add_passkey(c, key, "Telefono")).status_code == 201
    assert [p["name"] for p in (await c.get("/api/account/passkeys")).json()] == ["Telefono"]
    await c.aclose()

    # The invite is used: gone from the list, the link is dead; the password and the passkey sign in.
    assert (await admin.get("/api/invites")).json() == []
    async with SessionLocal() as db:
        row = await db.get(Invite, inv["id"])
        assert row is not None and row.used_at is not None and row.used_by == u.id
    assert (await anon.get(f"/api/invite/{token}")).status_code == 404
    c2 = await browser(app)
    assert (await accept(c2, token, "inv-anna2")).status_code == 404
    await c2.aclose()
    assert await login(app, "inv-anna") == 200
    c3, r3 = await sign_in(app, key)
    assert r3.status_code == 200 and r3.json()["username"] == "inv-anna"
    await c3.aclose()


async def test_a_link_works_once(app, admin):
    token = (await invite(admin, role="viewer"))["token"]
    c = await browser(app)
    assert (await accept(c, token, "inv-a")).status_code == 200
    assert (await accept(c, token, "inv-b")).status_code == 404
    assert (await user("inv-a")).role == "viewer" and await user("inv-b") is None
    await c.aclose()


async def test_two_browsers_racing_on_one_link_make_one_account(app, admin):
    token = (await invite(admin))["token"]
    a, b = await browser(app), await browser(app)
    ra, rb = await asyncio.gather(accept(a, token, "inv-x"), accept(b, token, "inv-y"))
    assert sorted([ra.status_code, rb.status_code]) == [200, 404], (ra.text, rb.text)
    assert [await user("inv-x") is not None, await user("inv-y") is not None].count(True) == 1
    await a.aclose()
    await b.aclose()


async def test_expired_revoked_and_unknown_links_are_404(app, admin, anon):
    expired = await invite(admin)
    async with SessionLocal() as db:
        await db.execute(update(Invite).where(Invite.id == expired["id"]).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        await db.commit()
    revoked = await invite(admin)
    assert (await admin.delete(f"/api/invites/{revoked['id']}")).status_code == 200
    assert (await admin.delete(f"/api/invites/{revoked['id']}")).status_code == 404
    assert (await admin.get("/api/invites")).json() == []
    c = await browser(app)
    for token in (expired["token"], revoked["token"], "nope"):
        assert (await anon.get(f"/api/invite/{token}")).status_code == 404
        assert (await accept(c, token, "inv-late")).status_code == 404
    assert await user("inv-late") is None
    await c.aclose()
    # A used invite can't be revoked.
    used = await invite(admin)
    c = await browser(app)
    assert (await accept(c, used["token"], "inv-used")).status_code == 200
    await c.aclose()
    assert (await admin.delete(f"/api/invites/{used['id']}")).status_code == 404


async def test_guessing_tokens_is_rate_limited(app, anon):
    for _ in range(10):
        assert (await anon.get("/api/invite/guess")).status_code == 404
    assert (await anon.get("/api/invite/guess")).status_code == 429


async def test_a_bad_username_or_password_leaves_the_invite_usable(app, admin):
    token = (await invite(admin))["token"]
    c = await browser(app)
    assert (await accept(c, token, "admin")).status_code == 409
    assert (await accept(c, token, "no spaces")).status_code == 422
    assert (await accept(c, token, None)).status_code == 422
    assert (await accept(c, token, "inv-short", "corta")).status_code == 422
    assert await user("inv-short") is None
    assert len((await admin.get("/api/invites")).json()) == 1
    assert (await accept(c, token, "inv-other")).status_code == 200
    await c.aclose()


async def test_the_public_routes_need_the_csrf_token_and_the_site_origin(app, admin):
    token = (await invite(admin))["token"]
    c = await browser(app)
    body = {"username": "inv-z", "password": PASSWORD}
    assert (await c.post(f"/api/invite/{token}/accept", headers={**HEADERS, "x-csrf-token": "x"}, json=body)).status_code == 403
    assert (await c.post(f"/api/invite/{token}/accept", headers={"origin": "http://evil.example"}, json=body)).status_code == 403
    assert await user("inv-z") is None
    await c.aclose()


async def test_only_admins_invite(app, editor, viewer, anon):
    for c in (editor, viewer):
        assert (await c.post("/api/invites", json={"role": "viewer"})).status_code == 403
        assert (await c.get("/api/invites")).status_code == 403
    assert (await anon.get("/api/invites")).status_code == 404
    assert (await anon.post("/api/invites", json={"role": "admin"})).status_code == 404


async def passkey_only(app, admin, username: str):
    """A user with only a passkey, as the invites made them before they asked for a password: (their signed-in
    client, their passkey, their id)."""
    c, key = await browser(app), Authenticator()
    assert (await accept(c, (await invite(admin))["token"], username)).status_code == 200
    c.csrf = (await c.get("/api/auth/me")).json()["csrf"]
    assert (await add_passkey(c, key)).status_code == 201
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.username == username).values(password_hash=None))
        await db.commit()
    return c, key, (await user(username)).id


async def test_a_passkey_only_user_keeps_their_last_passkey(app, admin):
    c, _, _ = await passkey_only(app, admin, "inv-solo")
    only = (await c.get("/api/account/passkeys")).json()[0]
    r = await c.delete(f"/api/account/passkeys/{only['id']}")
    assert r.status_code == 409 and "unica passkey" in r.json()["detail"]
    assert (await c.post("/api/account/totp/setup")).status_code == 400  # no password, no second factor for it
    # With a second one, either can go (but not both).
    added = (await add_passkey(c, Authenticator())).json()
    assert (await c.delete(f"/api/account/passkeys/{only['id']}")).status_code == 200
    assert (await c.delete(f"/api/account/passkeys/{added['id']}")).status_code == 409
    await c.aclose()


async def test_a_passkey_only_user_sets_a_password_and_then_signs_in_with_it(app, admin):
    c, _, _ = await passkey_only(app, admin, "inv-pw")
    assert await login(app, "inv-pw", "una-password-nuova") == 401
    # No current password to give: the passkey session is enough.
    assert (await c.post("/api/account/password", json={"new_password": "corta"})).status_code == 422
    assert (await c.post("/api/account/password", json={"new_password": "una-password-nuova"})).status_code == 200
    assert (await c.get("/api/auth/me")).json()["has_password"] is True
    assert await login(app, "inv-pw", "una-password-nuova") == 200
    # From now on, changing it asks for the current one; the last passkey may go, and TOTP may be set up.
    r = await c.post("/api/account/password", json={"current_password": "sbagliata", "new_password": "un'altra-password"})
    assert r.status_code == 400
    only = (await c.get("/api/account/passkeys")).json()[0]
    assert (await c.delete(f"/api/account/passkeys/{only['id']}")).status_code == 200
    assert (await c.post("/api/account/totp/setup")).status_code == 200
    await c.aclose()


# --------------------------------------------------------------------------- recovery links


async def invited(app, admin, username: str):
    """A user made through an invite, with a passkey too: (their signed-in client, their passkey, their id)."""
    c, key = await browser(app), Authenticator()
    assert (await accept(c, (await invite(admin))["token"], username)).status_code == 200
    c.csrf = (await c.get("/api/auth/me")).json()["csrf"]
    assert (await add_passkey(c, key)).status_code == 201
    return c, key, (await user(username)).id


async def test_a_recovery_link_sets_a_new_password_and_signs_out_the_others(app, admin, anon):
    old, key, uid = await invited(app, admin, "inv-lost")
    link = (await admin.post(f"/api/users/{uid}/recovery"))
    assert link.status_code == 201, link.text
    rec = link.json()
    assert rec["recovery"] is True and rec["username"] == "inv-lost" and rec["role"] == "editor" and rec["path"] == f"/invito/{rec['token']}"
    listed = (await admin.get("/api/invites")).json()
    assert [(i["id"], i["recovery"], i["username"]) for i in listed] == [(rec["id"], True, "inv-lost")] and "token" not in listed[0]

    info = (await anon.get(f"/api/invite/{rec['token']}")).json()
    assert info["recovery"] is True and info["username"] == "inv-lost" and info["role"] == "editor"

    # No username is taken: the password is that user's.
    c = await browser(app)
    r = await accept(c, rec["token"], "inv-other", "la-nuova-password")
    assert r.status_code == 200 and r.json() == {"ok": True, "username": "inv-lost"}, r.text

    # Signed in as that user; no account was made, the old browser is signed out, the old password is gone, the passkey stays.
    me = (await c.get("/api/auth/me")).json()
    assert me["authenticated"] and me["username"] == "inv-lost"
    assert (await user("inv-other")) is None and (await user("inv-lost")).role == "editor"
    assert (await old.get("/api/auth/me")).json()["authenticated"] is False
    async with SessionLocal() as db:
        row = await db.get(Invite, rec["id"])
        assert row is not None and row.used_at is not None and row.used_by == uid
    assert await login(app, "inv-lost") == 401
    assert await login(app, "inv-lost", "la-nuova-password") == 200
    c2, r2 = await sign_in(app, key)
    assert r2.status_code == 200 and r2.json()["username"] == "inv-lost"

    # Used: gone from the list, dead link.
    assert (await admin.get("/api/invites")).json() == []
    assert (await anon.get(f"/api/invite/{rec['token']}")).status_code == 404
    assert (await accept(c, rec["token"])).status_code == 404
    for client in (old, c, c2):
        await client.aclose()


async def test_a_recovery_link_works_once_and_a_new_one_replaces_it(app, admin):
    old, _, uid = await invited(app, admin, "inv-twice")
    await old.aclose()
    first = (await admin.post(f"/api/users/{uid}/recovery")).json()
    rec = (await admin.post(f"/api/users/{uid}/recovery")).json()
    assert [i["id"] for i in (await admin.get("/api/invites")).json()] == [rec["id"]]
    c = await browser(app)
    assert (await c.get(f"/api/invite/{first['token']}")).status_code == 404
    assert (await accept(c, rec["token"], password="la-prima-nuova")).status_code == 200
    assert (await accept(c, rec["token"], password="la-seconda-nuova")).status_code == 404
    assert await login(app, "inv-twice", "la-prima-nuova") == 200
    await c.aclose()


async def test_no_recovery_for_a_disabled_or_missing_user(app, admin, anon):
    old, _, uid = await invited(app, admin, "inv-off")
    await old.aclose()
    assert (await admin.post("/api/users/999999/recovery")).status_code == 404
    # Disabling drops the pending link; a disabled user gets none.
    assert (await admin.post(f"/api/users/{uid}/recovery")).status_code == 201
    assert (await admin.post(f"/api/users/{uid}/disable")).status_code == 200
    assert (await admin.get("/api/invites")).json() == []
    assert (await admin.post(f"/api/users/{uid}/recovery")).status_code == 409
    assert (await admin.post(f"/api/users/{uid}/enable")).status_code == 200

    # Disabled after the link was made: the accept is a 404 and the link isn't used up.
    rec = (await admin.post(f"/api/users/{uid}/recovery")).json()
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.id == uid).values(disabled_at=datetime.now(UTC)))
        await db.commit()
    c = await browser(app)
    assert (await accept(c, rec["token"], password="la-nuova-password")).status_code == 404
    assert (await anon.get(f"/api/invite/{rec['token']}")).status_code == 404
    async with SessionLocal() as db:
        assert (await db.get(Invite, rec["id"])).used_at is None
        await db.execute(update(User).where(User.id == uid).values(disabled_at=None))
        await db.commit()
    assert await login(app, "inv-off", "la-nuova-password") == 401
    assert (await accept(c, rec["token"], password="la-nuova-password")).status_code == 200
    async with SessionLocal() as db:
        assert len((await db.execute(select(Passkey.id).where(Passkey.user_id == uid))).all()) == 1
    await c.aclose()


async def test_a_recovery_link_can_be_revoked_and_only_admins_make_them(app, admin, editor, viewer, anon):
    old, _, uid = await invited(app, admin, "inv-rev")
    await old.aclose()
    rec = (await admin.post(f"/api/users/{uid}/recovery")).json()
    assert (await admin.delete(f"/api/invites/{rec['id']}")).status_code == 200
    assert (await admin.get("/api/invites")).json() == []
    assert (await anon.get(f"/api/invite/{rec['token']}")).status_code == 404
    for c in (editor, viewer):
        assert (await c.post(f"/api/users/{uid}/recovery")).status_code == 403
    assert (await anon.post(f"/api/users/{uid}/recovery")).status_code == 404
