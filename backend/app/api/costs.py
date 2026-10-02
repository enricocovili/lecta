"""AI call log and costs (provider, model, tokens, cost; no payloads are stored)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import AuditLog, Job
from ..security.auth import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


def audit_out(r: AuditLog) -> dict[str, Any]:
    return {
        "id": r.id,
        "ts": r.ts,
        "provider": r.provider_name,
        "provider_type": r.provider_type,
        "model": r.model,
        "role": r.role,
        "task": r.task,
        "request_key": r.request_key,
        "status": r.status,
        "error": r.error,
        "tokens_in": r.tokens_in,
        "tokens_out": r.tokens_out,
        "cost_usd": r.cost_usd,
        "duration_ms": r.duration_ms,
        "job_id": r.job_id,
        "chat_session_id": r.chat_session_id,
    }


@router.get("/audit")
async def list_audit(
    limit: int = Query(100, ge=1, le=1000), before: int | None = None, job: int | None = None, db: AsyncSession = Depends(get_db)
) -> list[dict]:
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if before:
        q = q.where(AuditLog.id < before)
    if job:
        q = q.where(AuditLog.job_id == job)
    return [audit_out(r) for r in (await db.execute(q)).scalars()]


@router.get("/audit/{audit_id}")
async def get_audit(audit_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    r = await db.get(AuditLog, audit_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return audit_out(r)


@router.get("/costs")
async def costs(db: AsyncSession = Depends(get_db)) -> dict:
    month = func.date_trunc("month", AuditLog.ts)
    rows = (
        await db.execute(
            select(month, AuditLog.provider_name, func.sum(AuditLog.tokens_in), func.sum(AuditLog.tokens_out),
                   func.sum(AuditLog.cost_usd), func.count())
            .where(AuditLog.status == "ok")
            .group_by(month, AuditLog.provider_name)
            .order_by(month.desc())
        )
    ).all()
    by_job = (
        await db.execute(
            select(Job.id, Job.title, Job.tokens_in, Job.tokens_out, Job.cost_usd, Job.created_at)
            .where((Job.tokens_in + Job.tokens_out) > 0)
            .order_by(Job.id.desc())
            .limit(100)
        )
    ).all()
    return {
        "months": [
            {"month": m, "provider": p, "tokens_in": int(ti or 0), "tokens_out": int(to or 0), "cost_usd": float(c or 0), "requests": n}
            for m, p, ti, to, c, n in rows
        ],
        "jobs": [
            {"id": i, "title": t, "tokens_in": ti, "tokens_out": to, "cost_usd": c, "created_at": ca} for i, t, ti, to, c, ca in by_job
        ],
    }
