"""Invite links: an admin creates one, whoever opens it creates an account with a passkey (a software authenticator
stands in for the browser)."""

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


async def accept(c, token: str, username: str, key: Authenticator):
    """Asks for the ceremony and answers it: the accept response."""
    start = await c.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": username})
    assert start.status_code == 200, start.text
    body = start.json()
    return await c.post(f"/api/invite/{token}/accept", headers=HEADERS,
                        json={"state": body["state"], "credential": key.create(body["options"]), "username": username})


async def user(username: str) -> User | None:
    async with SessionLocal() as db:
        return (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()


async def test_an_invite_makes_a_passkey_only_account_with_its_role(app, admin, anon):
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
    start = await c.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": "inv-anna"})
    assert start.status_code == 200, start.text
    opts = start.json()["options"]
    assert opts["user"]["name"] == "inv-anna" and opts["authenticatorSelection"]["residentKey"] == "required"
    assert opts["authenticatorSelection"]["userVerification"] == "required"
    # Asking for the ceremony doesn't use the link up.
    assert (await anon.get(f"/api/invite/{token}")).status_code == 200
    key = Authenticator()
    r = await c.post(f"/api/invite/{token}/accept", headers=HEADERS,
                     json={"state": start.json()["state"], "credential": key.create(opts), "username": "inv-anna", "name": "Telefono"})
    assert r.status_code == 200 and r.json() == {"ok": True, "username": "inv-anna"}, r.text

    # Signed in as the new user, who has the invite's role, no password, and the passkey.
    me = (await c.get("/api/auth/me")).json()
    assert me["authenticated"] and me["username"] == "inv-anna"
    u = await user("inv-anna")
    assert u is not None and u.role == "editor" and u.password_hash is None
    c.csrf = me["csrf"]
    assert [p["name"] for p in (await c.get("/api/account/passkeys")).json()] == ["Telefono"]
    assert (await c.get("/api/invites")).status_code == 403
    await c.aclose()

    # The invite is used: gone from the list, the link is dead, the passkey signs in.
    assert (await admin.get("/api/invites")).json() == []
    async with SessionLocal() as db:
        row = await db.get(Invite, inv["id"])
        assert row is not None and row.used_at is not None and row.used_by == u.id
    assert (await anon.get(f"/api/invite/{token}")).status_code == 404
    c2 = await browser(app)
    assert (await c2.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": "inv-anna2"})).status_code == 404
    await c2.aclose()
    c3, r3 = await sign_in(app, key)
    assert r3.status_code == 200 and r3.json()["username"] == "inv-anna"
    await c3.aclose()


async def test_a_link_works_once(app, admin):
    token = (await invite(admin, role="viewer"))["token"]
    c = await browser(app)
    start = (await c.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": "inv-a"})).json()
    again = (await c.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": "inv-b"})).json()
    key = Authenticator()
    assert (await c.post(f"/api/invite/{token}/accept", headers=HEADERS,
                         json={"state": start["state"], "credential": key.create(start["options"]), "username": "inv-a"})).status_code == 200
    # A second ceremony started before the first ended can't use the link again.
    r = await c.post(f"/api/invite/{token}/accept", headers=HEADERS,
                     json={"state": again["state"], "credential": Authenticator().create(again["options"]), "username": "inv-b"})
    assert r.status_code == 404
    assert (await user("inv-a")).role == "viewer" and await user("inv-b") is None
    await c.aclose()


async def test_two_browsers_racing_on_one_link_make_one_account(app, admin):
    token = (await invite(admin))["token"]
    a, b = await browser(app), await browser(app)
    sa = (await a.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": "inv-x"})).json()
    sb = (await b.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": "inv-y"})).json()
    ra, rb = await asyncio.gather(
        a.post(f"/api/invite/{token}/accept", headers=HEADERS, json={"state": sa["state"], "credential": Authenticator().create(sa["options"]), "username": "inv-x"}),
        b.post(f"/api/invite/{token}/accept", headers=HEADERS, json={"state": sb["state"], "credential": Authenticator().create(sb["options"]), "username": "inv-y"}),
    )
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
        assert (await c.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": "inv-late"})).status_code == 404
        r = await c.post(f"/api/invite/{token}/accept", headers=HEADERS, json={"state": "x", "credential": {"id": "x"}, "username": "inv-late"})
        assert r.status_code == 404
    assert await user("inv-late") is None
    await c.aclose()
    # A used invite can't be revoked.
    used = await invite(admin)
    c = await browser(app)
    assert (await accept(c, used["token"], "inv-used", Authenticator())).status_code == 200
    await c.aclose()
    assert (await admin.delete(f"/api/invites/{used['id']}")).status_code == 404


async def test_guessing_tokens_is_rate_limited(app, anon):
    for _ in range(10):
        assert (await anon.get("/api/invite/guess")).status_code == 404
    assert (await anon.get("/api/invite/guess")).status_code == 429


async def test_a_taken_username_leaves_the_invite_usable(app, admin):
    token = (await invite(admin))["token"]
    c = await browser(app)
    assert (await c.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": "admin"})).status_code == 409
    assert (await c.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": "no spaces"})).status_code == 422
    # Taken between the ceremony and the answer: nothing is made, the link still works.
    start = (await c.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": "inv-late"})).json()
    async with SessionLocal() as db:
        db.add(User(username="inv-late", password_hash=None, role="viewer"))
        await db.commit()
    key = Authenticator()
    r = await c.post(f"/api/invite/{token}/accept", headers=HEADERS,
                     json={"state": start["state"], "credential": key.create(start["options"]), "username": "inv-late"})
    assert r.status_code == 409
    async with SessionLocal() as db:
        assert (await db.execute(select(Passkey.id).join(User).where(User.username == "inv-late"))).first() is None
    assert len((await admin.get("/api/invites")).json()) == 1
    assert (await accept(c, token, "inv-other", key)).status_code == 200
    await c.aclose()


async def test_the_public_routes_need_the_csrf_token_and_the_site_origin(app, admin):
    token = (await invite(admin))["token"]
    c = await browser(app)
    assert (await c.post(f"/api/invite/{token}/options", headers={**HEADERS, "x-csrf-token": "x"}, json={"username": "inv-z"})).status_code == 403
    assert (await c.post(f"/api/invite/{token}/options", headers={"origin": "http://evil.example"}, json={"username": "inv-z"})).status_code == 403
    # The answer is bound to the username it was asked for.
    start = (await c.post(f"/api/invite/{token}/options", headers=HEADERS, json={"username": "inv-z"})).json()
    r = await c.post(f"/api/invite/{token}/accept", headers=HEADERS,
                     json={"state": start["state"], "credential": Authenticator().create(start["options"]), "username": "inv-w"})
    assert r.status_code == 400
    await c.aclose()


async def test_only_admins_invite(app, editor, viewer, anon):
    for c in (editor, viewer):
        assert (await c.post("/api/invites", json={"role": "viewer"})).status_code == 403
        assert (await c.get("/api/invites")).status_code == 403
    assert (await anon.get("/api/invites")).status_code == 404
    assert (await anon.post("/api/invites", json={"role": "admin"})).status_code == 404


async def test_a_passkey_only_user_keeps_their_last_passkey(app, admin):
    token = (await invite(admin))["token"]
    c = await browser(app)
    assert (await accept(c, token, "inv-solo", Authenticator())).status_code == 200
    c.csrf = (await c.get("/api/auth/me")).json()["csrf"]
    only = (await c.get("/api/account/passkeys")).json()[0]
    r = await c.delete(f"/api/account/passkeys/{only['id']}")
    assert r.status_code == 409 and "unica passkey" in r.json()["detail"]
    assert (await c.post("/api/account/totp/setup")).status_code == 400  # no password, no second factor for it
    # With a second one, either can go (but not both).
    key = Authenticator()
    start = (await c.post("/api/account/passkeys/options", headers=HEADERS)).json()
    added = (await c.post("/api/account/passkeys", headers=HEADERS, json={"state": start["state"], "credential": key.create(start["options"])})).json()
    assert (await c.delete(f"/api/account/passkeys/{only['id']}")).status_code == 200
    assert (await c.delete(f"/api/account/passkeys/{added['id']}")).status_code == 409
    await c.aclose()
