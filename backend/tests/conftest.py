"""Test fixtures.

Tests run inside the backend container (see scripts/test.sh): they use a
throw-away database `lecta_test_<pid>` on the same Postgres server, a temporary
data dir, and a private sub-directory of the shared latex volume, so the real
compile service is exercised over its unix socket.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import asyncpg

_RUN = uuid.uuid4().hex[:8]
_TEST_DB = f"lecta_test_{_RUN}"
_DATA = Path(tempfile.mkdtemp(prefix="lecta-test-data-"))
_LATEX = Path(os.environ.get("LECTA_LATEX_ROOT", "/latex-work")) / f"test-{_RUN}"


def _base_dsn() -> str:
    from app.config import Config

    return Config.database_url().replace("postgresql+asyncpg://", "postgresql://")


_ADMIN_DSN = _base_dsn()
os.environ["LECTA_DATABASE_URL"] = _ADMIN_DSN.rsplit("/", 1)[0].replace("postgresql://", "postgresql+asyncpg://") + "/" + _TEST_DB
os.environ["LECTA_DATA_DIR"] = str(_DATA)
os.environ["LECTA_LATEX_WORK"] = str(_LATEX)
os.environ["LECTA_COOKIE_SECURE"] = "never"

# Reload config with the test environment before anything imports the engine.
import app.config as _cfg  # noqa: E402

_cfg.Config.database_url_override = os.environ["LECTA_DATABASE_URL"]
_cfg.Config.data_dir = _DATA
_cfg.Config.latex_work_dir = _LATEX
_cfg.Config.cookie_secure = "never"

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402


def _create_db() -> None:
    async def go() -> None:
        conn = await asyncpg.connect(_ADMIN_DSN)
        await conn.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB}"')
        await conn.execute(f'CREATE DATABASE "{_TEST_DB}"')
        await conn.close()

    asyncio.run(go())
    from alembic import command
    from alembic.config import Config as AlembicConfig

    command.upgrade(AlembicConfig(str(Path(__file__).parent.parent / "alembic.ini")), "head")


def _drop_db() -> None:
    async def go() -> None:
        conn = await asyncpg.connect(_ADMIN_DSN)
        await conn.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB}" WITH (FORCE)')
        await conn.close()

    asyncio.run(go())


def pytest_sessionstart(session) -> None:  # noqa: ANN001
    _LATEX.mkdir(parents=True, exist_ok=True)
    _create_db()


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ANN001
    try:
        _drop_db()
    finally:
        shutil.rmtree(_DATA, ignore_errors=True)
        shutil.rmtree(_LATEX, ignore_errors=True)


ADMIN_USER = "admin"
ADMIN_PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture(scope="session")
async def app():
    from app.main import app as fastapi_app

    yield fastapi_app
    from app.db import engine

    await engine.dispose()


class Client(httpx.AsyncClient):
    """httpx client that remembers the CSRF token like the browser does."""

    csrf: str = ""

    async def request(self, method, url, **kw):  # noqa: ANN001
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            headers = dict(kw.pop("headers", None) or {})
            token = self.csrf or self.cookies.get("lecta_csrf") or ""
            headers.setdefault("x-csrf-token", token)
            kw["headers"] = headers
        return await super().request(method, url, **kw)


def make_client(app) -> Client:  # noqa: ANN001
    return Client(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest_asyncio.fixture
async def anon(app):
    from app.security.guard import NOT_FOUND_LIMIT, anon_limiter

    NOT_FOUND_LIMIT.hits.clear()
    anon_limiter.hits.clear()
    async with make_client(app) as c:
        yield c


async def ensure_admin(app) -> None:  # noqa: ANN001
    from app.db import SessionLocal
    from app.models import AdminUser
    from app.security.auth import get_admin_user, hash_password

    async with SessionLocal() as db:
        if await get_admin_user(db) is None:
            db.add(AdminUser(username=ADMIN_USER, password_hash=hash_password(ADMIN_PASSWORD)))
            await db.commit()


async def login(client: Client) -> None:
    await client.get("/api/auth/csrf")
    r = await client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    me = (await client.get("/api/auth/me")).json()
    client.csrf = me["csrf"]


@pytest_asyncio.fixture
async def admin(app):
    from app.security.auth import login_global_limiter, login_limiter

    login_limiter.hits.clear()
    login_global_limiter.hits.clear()
    await ensure_admin(app)
    async with make_client(app) as c:
        await login(c)
        yield c


@pytest_asyncio.fixture
async def db():
    from app.db import SessionLocal

    async with SessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def worker():
    """An in-process worker bound to the test database."""
    from app.worker.runner import Worker

    w = Worker()
    task = asyncio.create_task(w.run())
    await asyncio.sleep(0.2)
    yield w
    w.stop()
    try:
        await asyncio.wait_for(task, timeout=10)
    except (TimeoutError, asyncio.CancelledError):
        task.cancel()


async def wait_job(client: Client, job_id: int, timeout: float = 120, until=("succeeded", "failed", "cancelled")) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        j = (await client.get(f"/api/jobs/{job_id}")).json()
        if j["status"] in until:
            return j
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"job {job_id} did not finish: {j['status']} {j.get('progress_text')}\n{j.get('logs')}")
        await asyncio.sleep(0.2)


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"
