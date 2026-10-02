"""What a job handler receives: logging, progress, memoised steps, cancellation."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from ..db import SessionLocal
from ..models import Job, JobLog, JobStep

log = logging.getLogger("lecta.worker")
T = TypeVar("T")

# CPU-bound work (PDF rendering, image preprocessing, pandoc, embeddings) is
# capped at 2 concurrent tasks for the whole worker; provider calls are async I/O.
CPU_SLOTS = 2
_cpu_pool = ThreadPoolExecutor(max_workers=CPU_SLOTS, thread_name_prefix="cpu")
_cpu_sem: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    global _cpu_sem
    if _cpu_sem is None:
        _cpu_sem = asyncio.Semaphore(CPU_SLOTS)
    return _cpu_sem


async def run_cpu(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    async with _sem():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_cpu_pool, lambda: fn(*args, **kwargs))


class JobFailed(Exception):
    pass


class JobContext:
    def __init__(self, job: Job):
        self.job_id: int = job.id
        self.kind: str = job.kind
        self.payload: dict[str, Any] = dict(job.payload or {})
        self.course_id: int | None = job.course_id
        self.attempt: int = job.attempts
        self._last_progress = 0.0

    async def log(self, message: str, level: str = "info", **context: Any) -> None:
        """Append a log line; keyword arguments are stored as structured context
        (e.g. stage="vision", item="deck.pdf · p. 3", figure="f3x2", kind="rate_limit", audit_id=12)."""
        log.info("job %s: %s", self.job_id, message)
        ctx = {k: v for k, v in context.items() if v is not None} or None
        async with SessionLocal() as db:
            db.add(JobLog(job_id=self.job_id, level=level, message=message[:20000], context=ctx))
            await db.execute(update(Job).where(Job.id == self.job_id).values(updated_at=datetime.now(UTC)))
            await db.commit()

    async def progress(self, fraction: float | None, text: str | None = None) -> None:
        """Update the progress bar (None keeps the current fraction) and/or its text."""
        values: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if fraction is not None:
            values["progress"] = max(0.0, min(1.0, fraction))
        if text is not None:
            values["progress_text"] = text[:500]
        async with SessionLocal() as db:
            await db.execute(update(Job).where(Job.id == self.job_id).values(**values))
            await db.commit()

    async def get_step(self, key: str) -> tuple[bool, Any]:
        async with SessionLocal() as db:
            row = (
                await db.execute(select(JobStep).where(JobStep.job_id == self.job_id, JobStep.key == key))
            ).scalar_one_or_none()
            return (True, row.result) if row else (False, None)

    async def set_step(self, key: str, result: Any) -> None:
        # Round-trip through JSON so what we return now equals what a resume returns.
        result = json.loads(json.dumps(result, default=str))
        async with SessionLocal() as db:
            stmt = insert(JobStep).values(job_id=self.job_id, key=key, result=result)
            stmt = stmt.on_conflict_do_update(index_elements=["job_id", "key"], set_={"result": stmt.excluded.result})
            await db.execute(stmt)
            await db.commit()

    async def step(self, key: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        """Run fn once per job; on re-runs (retry/restart) return the stored result."""
        done, result = await self.get_step(key)
        if done:
            return result
        result = await fn()
        await self.set_step(key, result)
        return json.loads(json.dumps(result, default=str))

    async def add_usage(self, tokens_in: int, tokens_out: int, cost: float) -> None:
        async with SessionLocal() as db:
            await db.execute(
                update(Job)
                .where(Job.id == self.job_id)
                .values(
                    tokens_in=Job.tokens_in + tokens_in,
                    tokens_out=Job.tokens_out + tokens_out,
                    cost_usd=Job.cost_usd + cost,
                )
            )
            await db.commit()
