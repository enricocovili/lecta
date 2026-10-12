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
from .test_passkeys import HEADERS, Authenticator, b64u, sign_in


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
    assert me["authenticated"] and me["username"] == "inv-anna" and me["role"] == "editor" and me["has_password"] is False
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


# --------------------------------------------------------------------------- recovery links


async def invited(app, admin, username: str):
    """A passkey-only user made through an invite: (their signed-in client, their passkey, their id)."""
    c, key = await browser(app), Authenticator()
    assert (await accept(c, (await invite(admin))["token"], username, key)).status_code == 200
    c.csrf = (await c.get("/api/auth/me")).json()["csrf"]
    return c, key, (await user(username)).id


async def recover(c, token: str, key: Authenticator, **extra):
    start = await c.post(f"/api/invite/{token}/options", headers=HEADERS, json={})
    assert start.status_code == 200, start.text
    body = start.json()
    return await c.post(f"/api/invite/{token}/accept", headers=HEADERS, json={"state": body["state"], "credential": key.create(body["options"]), **extra})


async def test_a_recovery_link_adds_a_passkey_to_its_user_and_signs_out_the_others(app, admin, anon):
    old, lost, uid = await invited(app, admin, "inv-lost")
    link = (await admin.post(f"/api/users/{uid}/recovery"))
    assert link.status_code == 201, link.text
    rec = link.json()
    assert rec["recovery"] is True and rec["username"] == "inv-lost" and rec["role"] == "editor" and rec["path"] == f"/invito/{rec['token']}"
    listed = (await admin.get("/api/invites")).json()
    assert [(i["id"], i["recovery"], i["username"]) for i in listed] == [(rec["id"], True, "inv-lost")] and "token" not in listed[0]

    info = (await anon.get(f"/api/invite/{rec['token']}")).json()
    assert info["recovery"] is True and info["username"] == "inv-lost" and info["role"] == "editor"

    # No username is asked: the ceremony is for that user, and leaves out the passkeys they still have.
    c = await browser(app)
    start = await c.post(f"/api/invite/{rec['token']}/options", headers=HEADERS, json={"username": "inv-other"})
    opts = start.json()["options"]
    assert opts["user"]["name"] == "inv-lost" and [x["id"] for x in opts["excludeCredentials"]] == [b64u(lost.cred_id)]
    new = Authenticator()
    r = await c.post(f"/api/invite/{rec['token']}/accept", headers=HEADERS,
                     json={"state": start.json()["state"], "credential": new.create(opts), "name": "Telefono nuovo"})
    assert r.status_code == 200 and r.json() == {"ok": True, "username": "inv-lost"}, r.text

    # Signed in as that user, who now has both passkeys; no account was made, and the old browser is signed out.
    me = (await c.get("/api/auth/me")).json()
    assert me["authenticated"] and me["username"] == "inv-lost"
    c.csrf = me["csrf"]
    assert [p["name"] for p in (await c.get("/api/account/passkeys")).json()][1:] == ["Telefono nuovo"]
    assert (await user("inv-other")) is None and (await user("inv-lost")).role == "editor"
    assert (await old.get("/api/auth/me")).json()["authenticated"] is False
    async with SessionLocal() as db:
        row = await db.get(Invite, rec["id"])
        assert row is not None and row.used_at is not None and row.used_by == uid

    # Used: gone from the list, dead link; the new passkey signs in.
    assert (await admin.get("/api/invites")).json() == []
    assert (await anon.get(f"/api/invite/{rec['token']}")).status_code == 404
    assert (await c.post(f"/api/invite/{rec['token']}/options", headers=HEADERS, json={})).status_code == 404
    c2, r2 = await sign_in(app, new)
    assert r2.status_code == 200 and r2.json()["username"] == "inv-lost"
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
    a = (await c.post(f"/api/invite/{rec['token']}/options", headers=HEADERS, json={})).json()
    b = (await c.post(f"/api/invite/{rec['token']}/options", headers=HEADERS, json={})).json()
    assert (await c.post(f"/api/invite/{rec['token']}/accept", headers=HEADERS, json={"state": a["state"], "credential": Authenticator().create(a["options"])})).status_code == 200
    r = await c.post(f"/api/invite/{rec['token']}/accept", headers=HEADERS, json={"state": b["state"], "credential": Authenticator().create(b["options"])})
    assert r.status_code == 404
    async with SessionLocal() as db:
        assert len((await db.execute(select(Passkey.id).where(Passkey.user_id == uid))).all()) == 2
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

    # Disabled after the ceremony started: the accept is a 404 and the link isn't used up.
    rec = (await admin.post(f"/api/users/{uid}/recovery")).json()
    c = await browser(app)
    start = (await c.post(f"/api/invite/{rec['token']}/options", headers=HEADERS, json={})).json()
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.id == uid).values(disabled_at=datetime.now(UTC)))
        await db.commit()
    r = await c.post(f"/api/invite/{rec['token']}/accept", headers=HEADERS, json={"state": start["state"], "credential": Authenticator().create(start["options"])})
    assert r.status_code == 404
    assert (await anon.get(f"/api/invite/{rec['token']}")).status_code == 404
    assert (await c.post(f"/api/invite/{rec['token']}/options", headers=HEADERS, json={})).status_code == 404
    async with SessionLocal() as db:
        assert (await db.get(Invite, rec["id"])).used_at is None
        assert len((await db.execute(select(Passkey.id).where(Passkey.user_id == uid))).all()) == 1
        await db.execute(update(User).where(User.id == uid).values(disabled_at=None))
        await db.commit()
    assert (await recover(c, rec["token"], Authenticator())).status_code == 200
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
    # A plain invite still needs a username.
    c = await browser(app)
    assert (await c.post(f"/api/invite/{(await invite(admin))['token']}/options", headers=HEADERS, json={})).status_code == 422
    await c.aclose()
