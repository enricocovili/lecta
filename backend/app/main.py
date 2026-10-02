"""FastAPI application (served by uvicorn behind the frontend's /api proxy)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import appsecrets
from .api import auth, courses, dashboard, diag, jobs, public
from .config import config
from .security.guard import PrivateGuard, SecurityHeaders

log = logging.getLogger("lecta")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    secrets = appsecrets.load()
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import AdminUser

    async with SessionLocal() as db:
        if (await db.execute(select(AdminUser.id).limit(1))).first() is None:
            log.warning("No admin account yet. Open the site and use setup code: %s", secrets["setup_code"])
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Lecta",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    for module in (public, auth, courses, dashboard, jobs, diag):
        app.include_router(module.router, prefix="/api")

    from .api import extra_routers

    for r in extra_routers():
        app.include_router(r, prefix="/api")

    # Outermost first: headers wrap the guard, which wraps the routes.
    app.add_middleware(PrivateGuard)
    app.add_middleware(SecurityHeaders)
    return app


app = create_app()
