"""Jobs: list, detail, logs (filtered, text download, grouped problems), live events (SSE), cancel, retry, delete."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal, get_db
from ..models import AuditLog, IngestFigure, IngestItem, Job, JobLog
from ..security.auth import require_admin
from ..services import jobs as jobs_svc

router = APIRouter(dependencies=[Depends(require_admin)])


def job_out(j: Job) -> dict[str, Any]:
    return {
        "id": j.id,
        "kind": j.kind,
        "title": j.title,
        "status": j.status,
        "priority": j.priority,
        "progress": j.progress,
        "progress_text": j.progress_text,
        "error": j.error,
        "result": j.result,
        "attempts": j.attempts,
        "course_id": j.course_id,
        "parent_id": j.parent_id,
        "cost_usd": j.cost_usd,
        "tokens_in": j.tokens_in,
        "tokens_out": j.tokens_out,
        "cancel_requested": j.cancel_requested,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
    }


def log_out(entry: JobLog) -> dict[str, Any]:
    return {"id": entry.id, "ts": entry.ts, "level": entry.level, "message": entry.message, "context": entry.context}


async def _job(db: AsyncSession, job_id: int) -> Job:
    j = await db.get(Job, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return j


@router.get("/jobs")
async def list_jobs(
    status: str | None = Query(None, max_length=40),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    q = select(Job).order_by(Job.id.desc()).limit(limit)
    if status == "active":
        q = q.where(Job.status.in_(jobs_svc.ACTIVE))
    elif status:
        q = q.where(Job.status == status)
    return [job_out(j) for j in (await db.execute(q)).scalars()]


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    j = await _job(db, job_id)
    out = job_out(j)
    out["payload"] = j.payload
    logs = (
        await db.execute(select(JobLog).where(JobLog.job_id == j.id).order_by(JobLog.id.desc()).limit(300))
    ).scalars().all()
    out["logs"] = [log_out(x) for x in reversed(logs)]
    return out


LEVELS = {"info": ("info", "warn", "error"), "warn": ("warn", "error"), "error": ("error",)}


def _log_query(job_id: int, after: int, level: str | None, stage: str | None, q: str | None):
    stmt = select(JobLog).where(JobLog.job_id == job_id, JobLog.id > after)
    if level in LEVELS:
        stmt = stmt.where(JobLog.level.in_(LEVELS[level]))
    if stage:
        stmt = stmt.where(JobLog.context["stage"].astext == stage)
    if q:
        like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(JobLog.message.ilike(f"%{like}%"))
    return stmt.order_by(JobLog.id)


@router.get("/jobs/{job_id}/logs")
async def job_logs(
    job_id: int,
    after: int = 0,
    level: str | None = Query(None, pattern="^(info|warn|error)$"),
    stage: str | None = Query(None, max_length=40),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Log lines, oldest first; `level` is a minimum (warn = warnings and errors)."""
    await _job(db, job_id)
    rows = await db.execute(_log_query(job_id, after, level, stage, q).limit(limit))
    return [log_out(x) for x in rows.scalars()]


@router.get("/jobs/{job_id}/logs.txt")
async def job_logs_text(job_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    """The whole log as plain text (for downloading or sharing)."""
    j = await _job(db, job_id)
    rows = (await db.execute(_log_query(job_id, 0, None, None, None))).scalars()
    lines = [f"# job {j.id} · {j.kind} · {j.title} · {j.status}"]
    if j.error:
        lines.append(f"# error: {j.error.splitlines()[0]}")
    for x in rows:
        ctx = " ".join(f"{k}={v}" for k, v in (x.context or {}).items() if k not in ("item",))
        lines.append(f"{x.ts.isoformat(timespec='seconds')} {x.level.upper():5} {x.message}" + (f"  [{ctx}]" if ctx else ""))
    return Response(
        "\n".join(lines) + "\n", media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="job-{j.id}.log"', "Cache-Control": "no-store"},
    )


_NUM_RE = re.compile(r"\d+")
_AUDIT_KIND_RE = re.compile(r"^\[(\w+)\]")


def _signature(entry: JobLog) -> str:
    """What makes two problem lines "the same problem": the message without the page/figure it is about."""
    msg = entry.message
    item = (entry.context or {}).get("item")
    if item and msg.startswith(str(item)):
        msg = msg[len(str(item)):].lstrip(": ")
    figure = (entry.context or {}).get("figure")
    if figure:
        msg = msg.replace(str(figure), "…")
    return _NUM_RE.sub("#", msg)[:300]


@router.get("/jobs/{job_id}/problems")
async def job_problems(job_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Warnings/errors grouped by kind of problem, AI calls per task, figure outcomes."""
    j = await _job(db, job_id)
    rows = (
        await db.execute(select(JobLog).where(JobLog.job_id == job_id, JobLog.level.in_(("warn", "error"))).order_by(JobLog.id))
    ).scalars()
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for x in rows:
        c = x.context or {}
        key = (str(c.get("stage") or "other"), str(c.get("kind") or x.level), _signature(x))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "stage": key[0], "kind": key[1], "level": x.level, "message": x.message, "count": 0,
                "items": [], "audit_ids": [], "first_ts": x.ts, "last_ts": x.ts, "first_id": x.id, "task": c.get("task"),
                "provider": c.get("provider"), "model": c.get("model"),
            }
        g["count"] += 1
        g["last_ts"] = x.ts
        if x.level == "error":
            g["level"] = "error"
        item = c.get("item") or c.get("figure")
        if item and item not in g["items"] and len(g["items"]) < 50:
            g["items"].append(item)
        if c.get("audit_id") and len(g["audit_ids"]) < 5:
            g["audit_ids"].append(c["audit_id"])
    problems = sorted(groups.values(), key=lambda g: (g["level"] != "error", -g["count"], g["first_id"]))

    calls: dict[tuple[str, str], dict[str, Any]] = {}
    for a in (await db.execute(select(AuditLog).where(AuditLog.job_id == job_id).order_by(AuditLog.id))).scalars():
        k = (a.task or "?", f"{a.provider_name}/{a.model}")
        c = calls.setdefault(k, {"task": k[0], "target": k[1], "ok": 0, "error": 0, "other": 0, "ms": 0, "tokens_in": 0,
                                 "tokens_out": 0, "cost_usd": 0.0, "error_kinds": {}, "sample_error_id": None})
        if a.status == "ok":
            c["ok"] += 1
        elif a.status == "error":
            c["error"] += 1
            m = _AUDIT_KIND_RE.match(a.error or "")
            ek = m.group(1) if m else "unclassified"
            c["error_kinds"][ek] = c["error_kinds"].get(ek, 0) + 1
            c["sample_error_id"] = c["sample_error_id"] or a.id
        else:
            c["other"] += 1
        c["ms"] += a.duration_ms or 0
        c["tokens_in"] += a.tokens_in or 0
        c["tokens_out"] += a.tokens_out or 0
        c["cost_usd"] += a.cost_usd or 0.0
    for c in calls.values():
        n = c["ok"] + c["error"] + c["other"]
        c["avg_ms"] = int(c.pop("ms") / n) if n else 0

    figs = list((await db.execute(select(IngestFigure).where(IngestFigure.job_id == job_id).order_by(IngestFigure.id))).scalars())
    labels = dict((await db.execute(select(IngestItem.id, IngestItem.label).where(IngestItem.job_id == job_id))).all())
    by_status: dict[str, int] = {}
    by_origin: dict[str, int] = {}
    for f in figs:
        by_status[f.status] = by_status.get(f.status, 0) + 1
        by_origin[f.origin] = by_origin.get(f.origin, 0) + 1
    # Pictures that didn't end up in the notes (the model dropped them, or the job stopped before writing).
    troubled = [
        {"id": f.id, "key": f.key, "item": labels.get(f.item_id), "status": f.status, "origin": f.origin,
         "crop_url": f"/api/blobs/figures/{f.id}/crop" if f.crop_blob else None}
        for f in figs if f.status in ("dropped", "pending")
    ]
    return {
        "job": {"id": j.id, "status": j.status, "error": j.error, "at": j.progress_text},
        "problems": problems,
        "calls": sorted(calls.values(), key=lambda c: (-c["error"], c["task"])),
        "figures": {"total": len(figs), "by_status": by_status, "by_origin": by_origin, "troubled": troubled[:300]},
    }


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: int, request: Request, db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    """Server-sent events: `job` (state) and `log` (new lines), until the job ends."""
    await _job(db, job_id)

    async def gen():
        last_log = 0
        last_state = None
        while True:
            if await request.is_disconnected():
                return
            async with SessionLocal() as s:
                j = await s.get(Job, job_id)
                if j is None:
                    return
                state = json.dumps(job_out(j), default=str)
                logs = (
                    await s.execute(
                        select(JobLog).where(JobLog.job_id == job_id, JobLog.id > last_log).order_by(JobLog.id).limit(200)
                    )
                ).scalars().all()
            for entry in logs:
                last_log = entry.id
                yield f"event: log\ndata: {json.dumps(log_out(entry), default=str)}\n\n"
            if state != last_state:
                last_state = state
                yield f"event: job\ndata: {state}\n\n"
            if j.status in ("succeeded", "failed", "cancelled"):
                yield "event: end\ndata: {}\n\n"
                return
            yield ": ping\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    j = await _job(db, job_id)
    await jobs_svc.request_cancel(db, j)
    return job_out(j)


class RetryIn(BaseModel):
    from_scratch: bool = False


@router.post("/jobs/{job_id}/retry")
async def retry(job_id: int, body: RetryIn, db: AsyncSession = Depends(get_db)) -> dict:
    j = await _job(db, job_id)
    if j.status not in ("failed", "cancelled", "succeeded"):
        raise HTTPException(status_code=409, detail="Si possono riprovare solo le attività concluse")
    await jobs_svc.retry(db, j, from_scratch=body.from_scratch)
    return job_out(j)


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: int, purge: bool = False, db: AsyncSession = Depends(get_db)) -> dict:
    """Delete a finished job. `purge` also removes its upload."""
    j = await _job(db, job_id)
    if j.status not in jobs_svc.FINISHED:
        raise HTTPException(status_code=409, detail="Annulla l'attività prima di eliminarla")
    return {"deleted": [job_id], **await jobs_svc.delete_job(db, j, purge=purge)}


class BulkDeleteIn(BaseModel):
    status: str = Field(pattern="^(failed|cancelled|succeeded)$")
    purge: bool = False


@router.post("/jobs/delete")
async def delete_jobs(body: BulkDeleteIn, db: AsyncSession = Depends(get_db)) -> dict:
    """Delete every job with the given finished status."""
    rows = list((await db.execute(select(Job).where(Job.status == body.status).order_by(Job.id))).scalars())
    ids = [j.id for j in rows]
    totals = {"uploads": 0}
    for j in rows:
        for k, v in (await jobs_svc.delete_job(db, j, purge=body.purge)).items():
            totals[k] += v
    return {"deleted": ids, **totals}
