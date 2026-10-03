"""Schema migrations that drop a feature: they go forward and back on a scratch database."""

from __future__ import annotations

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
