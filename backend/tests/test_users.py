"""Settings → Utenti: the admins list the users, change their role, disable, re-enable, sign out and delete them; the last
active admin is always kept."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.models import Course, Passkey, Session, User

from .conftest import ADMIN_PASSWORD, ADMIN_USER, ensure_user, login, make_client


@pytest.fixture(autouse=True)
async def _restore():
    """The tests change roles and disable users that other tests rely on: put everyone back as they were."""
    async with SessionLocal() as db:
        before = {u.id: (u.role, u.disabled_at) for u in (await db.execute(select(User))).scalars()}
    yield
    async with SessionLocal() as db:
        for c in (await db.execute(select(Course).join(User, User.id == Course.owner_id).where(User.username.like("usr-%")))).scalars():
            await db.delete(c)
        await db.execute(delete(User).where(User.username.like("usr-%")))
        for uid, (role, disabled_at) in before.items():
            await db.execute(update(User).where(User.id == uid).values(role=role, disabled_at=disabled_at))
        await db.commit()


async def user_id(username: str) -> int:
    async with SessionLocal() as db:
        return (await db.execute(select(User.id).where(User.username == username))).scalar_one()


async def signed_in(app, username: str, role: str):
    await ensure_user(username, role)
    c = make_client(app)
    await login(c, username)
    return c


async def only_admin_left() -> None:
    """Leave `admin` as the only active admin."""
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.role == "admin", User.username != ADMIN_USER).values(role="editor"))
        await db.execute(update(User).where(User.username == ADMIN_USER).values(disabled_at=None))
        await db.commit()


async def test_the_list_says_who_each_user_is(app, admin):
    await ensure_user("usr-anna", "viewer")
    async with SessionLocal() as db:
        db.add(User(username="usr-key", password_hash=None, role="editor"))
        await db.commit()
        key = (await db.execute(select(User).where(User.username == "usr-key"))).scalar_one()
        db.add(Passkey(user_id=key.id, credential_id="usr-key-cred", public_key=b"k"))
        await db.commit()
    anna = await signed_in(app, "usr-anna", "viewer")
    await anna.aclose()
    rows = {u["username"]: u for u in (await admin.get("/api/users")).json()}
    assert rows["usr-anna"]["role"] == "viewer" and rows["usr-anna"]["has_password"] and rows["usr-anna"]["last_seen_at"]
    assert rows["usr-anna"]["passkeys"] == 0 and rows["usr-anna"]["courses"] == 0 and rows["usr-anna"]["disabled_at"] is None
    assert rows["usr-key"]["passkeys"] == 1 and not rows["usr-key"]["has_password"] and rows["usr-key"]["last_seen_at"] is None
    assert rows[ADMIN_USER]["you"] and not rows["usr-anna"]["you"]


async def test_a_new_role_counts_from_the_next_request(app, admin):
    ed = await signed_in(app, "usr-ed", "editor")
    assert (await ed.get("/api/users")).status_code == 403
    r = await admin.patch(f"/api/users/{await user_id('usr-ed')}", json={"role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert (await ed.get("/api/users")).status_code == 200
    assert (await admin.patch(f"/api/users/{await user_id('usr-ed')}", json={"role": "boss"})).status_code == 422
    assert (await admin.patch("/api/users/999999", json={"role": "viewer"})).status_code == 404
    await ed.aclose()


async def test_disabling_signs_out_and_blocks_the_sign_in_until_re_enabled(app, admin):
    ed = await signed_in(app, "usr-ed", "editor")
    uid = await user_id("usr-ed")
    r = await admin.post(f"/api/users/{uid}/disable")
    assert r.status_code == 200 and r.json()["disabled_at"]
    assert not (await ed.get("/api/auth/me")).json()["authenticated"]
    async with SessionLocal() as db:
        assert (await db.execute(select(Session).where(Session.user_id == uid))).first() is None
    await ed.get("/api/auth/csrf")
    assert (await ed.post("/api/auth/login", json={"username": "usr-ed", "password": ADMIN_PASSWORD})).status_code == 401

    assert (await admin.post(f"/api/users/{uid}/enable")).json()["disabled_at"] is None
    await login(ed, "usr-ed")
    assert (await ed.get("/api/auth/me")).json()["authenticated"]
    await ed.aclose()


async def test_sign_out_everywhere(app, admin):
    one = await signed_in(app, "usr-ed", "editor")
    two = make_client(app)
    await login(two, "usr-ed")
    assert (await admin.post(f"/api/users/{await user_id('usr-ed')}/logout")).status_code == 200
    for c in (one, two):
        assert not (await c.get("/api/auth/me")).json()["authenticated"]
        await c.aclose()
    assert (await admin.get("/api/auth/me")).json()["authenticated"]  # someone else's sessions only


async def test_a_user_with_courses_is_disabled_not_deleted(app, admin):
    ed = await signed_in(app, "usr-ed", "editor")
    course = (await ed.post("/api/courses", json={"name": "Corso di usr-ed", "chapters": ["Uno"]})).json()
    uid = await user_id("usr-ed")
    r = await admin.delete(f"/api/users/{uid}")
    assert r.status_code == 409 and "materie" in r.json()["detail"]
    assert (await ed.post(f"/api/courses/{course['id']}/delete", json={"confirm_slug": course["slug"]})).status_code == 200
    assert (await admin.delete(f"/api/users/{uid}")).status_code == 200
    assert not (await ed.get("/api/auth/me")).json()["authenticated"]
    assert all(u["username"] != "usr-ed" for u in (await admin.get("/api/users")).json())
    assert (await admin.delete(f"/api/users/{uid}")).status_code == 404
    await ed.aclose()


async def test_an_admin_does_not_delete_themselves(app, admin):
    other = await signed_in(app, "usr-boss", "admin")
    r = await other.delete(f"/api/users/{await user_id('usr-boss')}")
    assert r.status_code == 409
    await other.aclose()


async def test_the_last_active_admin_is_kept(app, admin):
    await only_admin_left()
    me = await user_id(ADMIN_USER)
    for call in (admin.patch(f"/api/users/{me}", json={"role": "editor"}), admin.post(f"/api/users/{me}/disable")):
        r = await call
        assert r.status_code == 409 and "ultimo amministratore" in r.json()["detail"]
    assert (await admin.patch(f"/api/users/{me}", json={"role": "admin"})).status_code == 200  # no change is fine

    # With another active admin, either may step down: then the other one is the last.
    other = await signed_in(app, "usr-boss", "admin")
    boss = await user_id("usr-boss")
    assert (await other.patch(f"/api/users/{boss}", json={"role": "editor"})).status_code == 200
    assert (await other.get("/api/users")).status_code == 403
    assert (await admin.patch(f"/api/users/{boss}", json={"role": "admin"})).status_code == 200
    assert (await admin.post(f"/api/users/{me}/disable")).status_code == 200  # signs the admin out too
    assert (await other.post(f"/api/users/{boss}/disable")).status_code == 409
    assert (await other.delete(f"/api/users/{boss}")).status_code == 409
    await other.aclose()


async def test_two_admins_demoting_each_other_at_once_leave_one(app, admin):
    await only_admin_left()
    other = await signed_in(app, "usr-boss", "admin")
    me, boss = await user_id(ADMIN_USER), await user_id("usr-boss")
    a, b = await asyncio.gather(
        admin.patch(f"/api/users/{boss}", json={"role": "editor"}), other.patch(f"/api/users/{me}", json={"role": "editor"})
    )
    assert sorted([a.status_code, b.status_code]) in ([200, 403], [200, 409]), (a.text, b.text)
    async with SessionLocal() as db:
        left = (await db.execute(select(User.username).where(User.role == "admin", User.disabled_at.is_(None)))).scalars().all()
    assert len(left) == 1
    await other.aclose()
