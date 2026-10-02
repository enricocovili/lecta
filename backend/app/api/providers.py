"""AI providers, model lists and pricing, prompts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import appsecrets
from ..db import get_db
from ..egress import gate
from ..models import Prompt, Provider
from ..security.auth import require_admin
from ..services import prompts as prompts_svc
from ..services import settings as settings_svc

router = APIRouter(dependencies=[Depends(require_admin)])


class ModelInfo(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    label: str | None = Field(None, max_length=200)
    input_per_mtok: float = Field(0, ge=0)
    output_per_mtok: float = Field(0, ge=0)
    vision: bool = True


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: str
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=4000)  # write-only
    models: list[ModelInfo] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ProviderPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=4000)
    clear_api_key: bool = False
    models: list[ModelInfo] | None = None
    options: dict[str, Any] | None = None
    enabled: bool | None = None


def provider_out(p: Provider) -> dict[str, Any]:
    key = appsecrets.decrypt(p.api_key_enc) if p.api_key_enc else None
    info = gate.provider_type_info(p.type)
    return {
        "id": p.id,
        "name": p.name,
        "type": p.type,
        "type_label": info["label"] if info else p.type,
        "base_url": p.base_url,
        "default_base_url": info["default_base_url"] if info else None,
        "has_api_key": bool(key),
        "api_key_hint": f"…{key[-4:]}" if key and len(key) > 8 else None,
        "models": p.models,
        "options": p.options,
        "enabled": p.enabled,
        "usable": gate.provider_allowed(p) is None,
        "unusable_reason": gate.provider_allowed(p),
        "last_test": p.last_test,
        "created_at": p.created_at,
    }


async def _provider(db: AsyncSession, pid: int) -> Provider:
    p = await db.get(Provider, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return p


def _check_url(url: str | None) -> None:
    if url and not (url.startswith("http://") or url.startswith("https://") or url.startswith("fake://")):
        raise HTTPException(status_code=422, detail="l'URL base deve iniziare con http:// o https://")


@router.get("/providers/types")
async def provider_types() -> list[dict]:
    return gate.provider_types()


@router.get("/providers")
async def list_providers(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(Provider).order_by(Provider.id))).scalars()
    return [provider_out(p) for p in rows]


@router.post("/providers", status_code=201)
async def create_provider(body: ProviderIn, db: AsyncSession = Depends(get_db)) -> dict:
    if gate.provider_type_info(body.type) is None:
        raise HTTPException(status_code=422, detail="tipo di provider sconosciuto")
    _check_url(body.base_url)
    p = Provider(
        name=body.name,
        type=body.type,
        base_url=body.base_url or None,
        api_key_enc=appsecrets.encrypt(body.api_key) if body.api_key else None,
        models=[m.model_dump() for m in body.models],
        options=body.options,
        enabled=body.enabled,
    )
    if body.type == "fake" and not p.models:
        p.models = [
            {"id": "fake-large", "label": "Fake large", "input_per_mtok": 1.0, "output_per_mtok": 5.0, "vision": True},
            {"id": "fake-small", "label": "Fake small", "input_per_mtok": 0.1, "output_per_mtok": 0.5, "vision": True},
        ]
    db.add(p)
    await db.commit()
    return provider_out(p)


@router.patch("/providers/{pid}")
async def patch_provider(pid: int, body: ProviderPatch, db: AsyncSession = Depends(get_db)) -> dict:
    p = await _provider(db, pid)
    data = body.model_dump(exclude_unset=True)
    if "base_url" in data:
        _check_url(data["base_url"])
        p.base_url = data["base_url"] or None
    for k in ("name", "options", "enabled"):
        if k in data and data[k] is not None:
            setattr(p, k, data[k])
    if body.models is not None:
        p.models = [m.model_dump() for m in body.models]
    if body.clear_api_key:
        p.api_key_enc = None
    elif body.api_key:
        p.api_key_enc = appsecrets.encrypt(body.api_key)
    p.updated_at = datetime.now(UTC)
    await db.commit()
    # A provider that became unusable (disabled) is removed from role assignments.
    if gate.provider_allowed(p):
        await _unassign(db, p.id)
    return provider_out(p)


async def _unassign(db: AsyncSession, pid: int) -> None:
    roles = (await settings_svc.get_section(db, "roles")).model_dump()
    changed = False
    for assignment in roles.values():
        for slot in ("primary", "fallback"):
            if assignment[slot].get("provider_id") == pid:
                assignment[slot] = {"provider_id": None, "model": None}
                changed = True
    if changed:
        await settings_svc.set_section(db, "roles", roles)


@router.delete("/providers/{pid}")
async def delete_provider(pid: int, db: AsyncSession = Depends(get_db)) -> dict:
    p = await _provider(db, pid)
    await _unassign(db, p.id)
    await db.delete(p)
    await db.commit()
    return {"ok": True}


@router.post("/providers/{pid}/test")
async def test_provider(pid: int, db: AsyncSession = Depends(get_db)) -> dict:
    await _provider(db, pid)
    try:
        return await gate.test_provider(pid)
    except gate.GateRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/providers/{pid}/models/fetch")
async def fetch_models(pid: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Fetch the provider's model list and merge it with the stored one (pricing is kept)."""
    await _provider(db, pid)
    try:
        res = await gate.test_provider(pid)
    except gate.GateRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if not res["ok"]:
        raise HTTPException(status_code=502, detail=res["detail"])
    async with db.begin_nested():
        p = await _provider(db, pid)
        existing = {m["id"]: m for m in p.models or []}
        for m in res["models"]:
            if m["id"] not in existing:
                existing[m["id"]] = {"id": m["id"], "label": m.get("label"), "input_per_mtok": 0, "output_per_mtok": 0,
                                     "vision": m.get("vision", True)}
        p.models = list(existing.values())
    await db.commit()
    return provider_out(p)


# --------------------------------------------------------------------------- prompts


@router.get("/prompts")
async def list_prompts(db: AsyncSession = Depends(get_db)) -> list[dict]:
    out = []
    for key, d in prompts_svc.DEFAULTS.items():
        versions = (await db.execute(select(Prompt).where(Prompt.key == key).order_by(Prompt.version.desc()))).scalars().all()
        active = next((v for v in versions if v.active), None)
        out.append(
            {
                "key": key,
                "label": d.label,
                "role": d.role,
                "is_default": active is None,
                "active_version": active.version if active else None,
                "text": active.text if active else d.text,
                "default_text": d.text,
                "versions": [{"version": v.version, "created_at": v.created_at, "active": v.active, "note": v.note} for v in versions],
            }
        )
    return out


class PromptIn(BaseModel):
    text: str = Field(min_length=1, max_length=100000)
    note: str | None = Field(None, max_length=500)


@router.put("/prompts/{key}")
async def save_prompt(key: str, body: PromptIn, db: AsyncSession = Depends(get_db)) -> dict:
    if key not in prompts_svc.DEFAULTS:
        raise HTTPException(status_code=404, detail="Not Found")
    p = await prompts_svc.save_prompt(db, key, body.text, body.note)
    return {"key": key, "version": p.version}


@router.post("/prompts/{key}/reset")
async def reset_prompt(key: str, db: AsyncSession = Depends(get_db)) -> dict:
    if key not in prompts_svc.DEFAULTS:
        raise HTTPException(status_code=404, detail="Not Found")
    await prompts_svc.reset_prompt(db, key)
    return {"key": key, "is_default": True}


@router.post("/prompts/{key}/versions/{version}/activate")
async def activate_prompt(key: str, version: int, db: AsyncSession = Depends(get_db)) -> dict:
    if key not in prompts_svc.DEFAULTS:
        raise HTTPException(status_code=404, detail="Not Found")
    await prompts_svc.activate_version(db, key, version)
    return {"key": key, "version": version}


@router.get("/prompts/{key}/versions/{version}")
async def get_prompt_version(key: str, version: int, db: AsyncSession = Depends(get_db)) -> dict:
    row = (await db.execute(select(Prompt).where(Prompt.key == key, Prompt.version == version))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return {"key": key, "version": version, "text": row.text, "note": row.note, "created_at": row.created_at}
