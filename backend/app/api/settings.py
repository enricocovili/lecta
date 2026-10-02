"""Settings sections (everything configurable from the web UI)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..security.auth import require_admin
from ..services import embeddings, semantic, templates
from ..services import jobs as jobs_svc
from ..services import settings as settings_svc

router = APIRouter(dependencies=[Depends(require_admin)])

# Sections written by the server itself, or edited through dedicated endpoints.
READONLY_FIELDS = {"embeddings": {"benchmark"}}


@router.get("/settings")
async def all_settings(db: AsyncSession = Depends(get_db)) -> dict:
    out = await settings_svc.all_sections(db)
    out["roles_meta"] = {"roles": list(settings_svc.ROLES), "labels": settings_svc.ROLE_LABELS}
    return out


@router.get("/settings/template/effective")
async def effective_template(db: AsyncSession = Depends(get_db)) -> dict:
    tpl = await settings_svc.get_section(db, "template")
    return {"preamble": tpl.preamble or templates.DEFAULT_PREAMBLE, "is_default": tpl.preamble is None}


@router.post("/settings/template/reset")
async def reset_template(db: AsyncSession = Depends(get_db)) -> dict:
    await settings_svc.set_section(db, "template", {"preamble": None})
    return {"preamble": templates.DEFAULT_PREAMBLE, "is_default": True}


@router.get("/settings/{section}")
async def get_settings(section: str, db: AsyncSession = Depends(get_db)) -> dict:
    if section not in settings_svc.SECTIONS:
        raise HTTPException(status_code=404, detail="Not Found")
    return (await settings_svc.get_section(db, section)).model_dump(mode="json")


@router.put("/settings/{section}")
async def put_settings(section: str, value: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)) -> dict:
    if section not in settings_svc.SECTIONS:
        raise HTTPException(status_code=404, detail="Not Found")
    for f in READONLY_FIELDS.get(section, ()):
        value.pop(f, None)
    if section == "roles":
        from ..egress import gate

        await gate.validate_role_settings(db, value)
    before = (await settings_svc.get_section(db, section)).model_dump(mode="json")
    try:
        saved = await settings_svc.set_section(db, section, value)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_url=False)) from e
    if section in ("template", "latex") and before != saved.model_dump(mode="json"):
        await _touch_published(db, section, before, saved.model_dump(mode="json"))
    if section == "embeddings" and (before["model"], before["threads"]) != (saved.model, saved.threads) and saved.model != "off":
        await jobs_svc.enqueue(db, "embeddings.benchmark", {}, title="Benchmark embeddings", priority="index")
    return saved.model_dump(mode="json")


async def _touch_published(db: AsyncSession, section: str, before: dict[str, Any], after: dict[str, Any]) -> None:
    """A new global preamble or engine changes the PDF of published courses that use it: republish them."""
    from datetime import UTC, datetime

    from sqlalchemy import update

    from ..models import Course

    q = update(Course).where(Course.published.is_(True))
    if section == "template":
        q = q.where(Course.preamble_override.is_(None))
    elif before.get("engine") != after.get("engine"):
        q = q.where(Course.engine.is_(None))
    else:
        return
    await db.execute(q.values(updated_at=datetime.now(UTC)))
    await db.commit()


@router.post("/settings/embeddings/benchmark")
async def run_benchmark(db: AsyncSession = Depends(get_db)) -> dict:
    job = await jobs_svc.enqueue(db, "embeddings.benchmark", {}, title="Benchmark embeddings", priority="interactive")
    return {"job_id": job.id}


@router.get("/index/status")
async def index_status(db: AsyncSession = Depends(get_db)) -> dict:
    st = await semantic.status(db)
    counts = (
        await db.execute(
            text(
                "SELECT (SELECT count(*) FROM index_chunks), (SELECT count(*) FROM index_chunks WHERE embedding IS NOT NULL),"
                " (SELECT count(*) FROM index_state), (SELECT count(*) FROM chapters)"
            )
        )
    ).one()
    return {
        "semantic": st,
        "mode": "hybrid" if st["enabled"] else "lexical",
        "chunks": counts[0], "embedded": counts[1], "chapters_indexed": counts[2], "chapters": counts[3],
        "available_models": embeddings.AVAILABLE,
    }


@router.post("/index/rebuild")
async def rebuild_index(db: AsyncSession = Depends(get_db)) -> dict:
    job = await jobs_svc.enqueue(db, "index.rebuild", {}, title="Ricostruzione dell'indice di ricerca", priority="index")
    return {"job_id": job.id}
