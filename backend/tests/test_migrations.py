"""Schema migrations that drop a feature: they go forward and back on a scratch database."""

from __future__ import annotations

import asyncpg
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from .test_rename_migrations import ALEMBIC_INI, run, scratch_db  # noqa: F401  (fixture)


def test_the_inbox_table_goes_and_comes_back(scratch_db):  # noqa: F811
    dsn, upgrade_to = scratch_db
    tables = "SELECT count(*) AS n FROM information_schema.tables WHERE table_name = 'inbox_items'"
    upgrade_to("0018")
    run(dsn, "INSERT INTO inbox_items (status, title, bundle) VALUES ('open', 'vecchio', '{}'::jsonb)")
    upgrade_to("0019")
    assert run(dsn, tables)[0]["n"] == 0
    command.downgrade(AlembicConfig(ALEMBIC_INI), "0018")
    assert run(dsn, tables)[0]["n"] == 1
    upgrade_to("head")
    assert run(dsn, tables)[0]["n"] == 0


def test_the_admin_becomes_the_first_user_with_the_admin_role(scratch_db):  # noqa: F811
    dsn, upgrade_to = scratch_db
    upgrade_to("0026")
    (row,) = run(dsn, "INSERT INTO admin_user (username, password_hash) VALUES ('me', 'h') RETURNING id")
    run(dsn, "INSERT INTO passkeys (user_id, credential_id, public_key) VALUES ($1, 'cred', '\\x00')", row["id"])
    run(dsn, "INSERT INTO sessions (id, user_id, csrf_token, expires_at) VALUES ('s', $1, 'c', now() + interval '1 day')", row["id"])
    upgrade_to("0027")
    (user,) = run(dsn, "SELECT id, username, role, disabled_at FROM users")
    assert (user["id"], user["username"], user["role"], user["disabled_at"]) == (row["id"], "me", "admin", None)
    # Passkeys and sessions still point at the user; new users need no password but a known role.
    assert run(dsn, "SELECT count(*) AS n FROM passkeys p JOIN users u ON u.id = p.user_id")[0]["n"] == 1
    assert run(dsn, "SELECT count(*) AS n FROM sessions s JOIN users u ON u.id = s.user_id")[0]["n"] == 1
    run(dsn, "INSERT INTO users (username, role) VALUES ('invited', 'viewer')")
    with pytest.raises(asyncpg.CheckViolationError):
        run(dsn, "INSERT INTO users (username, role) VALUES ('odd', 'owner')")
    command.downgrade(AlembicConfig(ALEMBIC_INI), "0026")
    assert [r["username"] for r in run(dsn, "SELECT username FROM admin_user")] == ["me"]
    upgrade_to("head")
    assert run(dsn, "SELECT role FROM users")[0]["role"] == "admin"


def test_marks_and_the_last_open_page_go_to_the_course_owner(scratch_db):  # noqa: F811
    dsn, upgrade_to = scratch_db
    upgrade_to("0032")
    (user,) = run(dsn, "INSERT INTO users (username, role) VALUES ('me', 'editor') RETURNING id")
    (course,) = run(dsn, "INSERT INTO courses (name, slug, owner_id) VALUES ('Segnali', 'segnali', $1) RETURNING id", user["id"])
    (lesson,) = run(dsn, "INSERT INTO lessons (course_id, number, title) VALUES ($1, 1, 'Lezione 1') RETURNING id", course["id"])
    pages = [r["id"] for r in run(dsn, """INSERT INTO lesson_pages (lesson_id, position, kind, unclear)
        VALUES ($1, 1, 'blank', false), ($1, 2, 'blank', true) RETURNING id""", lesson["id"])]
    run(dsn, "UPDATE lessons SET last_page_id = $1", pages[0])
    upgrade_to("0033")
    assert [(r["page_id"], r["user_id"]) for r in run(dsn, "SELECT * FROM lesson_marks")] == [(pages[1], user["id"])]
    assert [(r["lesson_id"], r["user_id"], r["page_id"]) for r in run(dsn, "SELECT * FROM lesson_places")] == [(lesson["id"], user["id"], pages[0])]
    command.downgrade(AlembicConfig(ALEMBIC_INI), "0032")
    assert [r["unclear"] for r in run(dsn, "SELECT unclear FROM lesson_pages ORDER BY position")] == [False, True]
    assert run(dsn, "SELECT last_page_id FROM lessons")[0]["last_page_id"] == pages[0]
    upgrade_to("head")
