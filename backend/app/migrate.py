"""Run Alembic migrations under a Postgres advisory lock (backend and worker both call this)."""

from __future__ import annotations

import asyncio
import logging
import time

import asyncpg
from alembic import command
from alembic.config import Config as AlembicConfig

from .config import config

LOCK_ID = 7_283_401


async def _wait_and_lock() -> asyncpg.Connection:
    dsn = config.database_url().replace("postgresql+asyncpg://", "postgresql://")
    deadline = time.monotonic() + 120
    while True:
        try:
            conn = await asyncpg.connect(dsn)
            break
        except Exception:
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(2)
    await conn.execute("SELECT pg_advisory_lock($1)", LOCK_ID)
    return conn


def main() -> None:
    logging.basicConfig(level="INFO")
    loop = asyncio.new_event_loop()
    conn = loop.run_until_complete(_wait_and_lock())
    try:
        command.upgrade(AlembicConfig("alembic.ini"), "head")
    finally:
        loop.run_until_complete(conn.execute("SELECT pg_advisory_unlock($1)", LOCK_ID))
        loop.run_until_complete(conn.close())
        loop.close()


if __name__ == "__main__":
    main()
