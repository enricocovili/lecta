"""Worker process: claims jobs from Postgres and runs their handlers.

* claim: UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)
* wake-up: LISTEN lecta_jobs (plus a 5 s poll as a fallback)
* heartbeat every 10 s; jobs whose worker died are re-queued on startup / when stale
* cancel: the heartbeat notices cancel_requested and cancels the asyncio task
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
from sqlalchemy import select, text, update

from ..config import config
from ..db import SessionLocal
from ..models import Job, JobLog
from ..services.jobs import CHANNEL
from .context import JobContext, JobFailed
from .registry import HANDLERS, load_all

log = logging.getLogger("lecta.worker")

MAX_CONCURRENT = int(os.environ.get("LECTA_WORKER_CONCURRENCY", "4"))
HEARTBEAT_S = 10
STALE_AFTER = timedelta(seconds=60)
ALIVE_FILE = Path(os.environ.get("LECTA_WORKER_ALIVE", "/tmp/worker-alive"))

CLAIM_SQL = text(
    """
    UPDATE jobs SET status = 'running', started_at = COALESCE(started_at, now()), heartbeat_at = now(),
                    updated_at = now(), attempts = attempts + 1
    WHERE id = (
        SELECT id FROM jobs WHERE status = 'queued'
        ORDER BY priority, id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id
    """
)


class Worker:
    def __init__(self) -> None:
        self.tasks: dict[int, asyncio.Task] = {}
        self.wakeup = asyncio.Event()
        self.stopping = False
        self._listener: asyncpg.Connection | None = None
        self.on_idle_hooks: list = []

    # ----------------------------------------------------------------- lifecycle

    async def run(self) -> None:
        from ..services import semantic

        semantic.ALLOW_LOAD = True  # the embedding model lives in the worker
        load_all()
        await self.recover_stale(all_running=True)
        await self._listen()
        from ..pipeline import startup

        startup_task = asyncio.create_task(startup.on_worker_start(self))
        hb = asyncio.create_task(self._heartbeat_loop())
        log.info("worker started (concurrency=%s, handlers=%s)", MAX_CONCURRENT, sorted(HANDLERS))
        try:
            while not self.stopping:
                await self._fill()
                self.wakeup.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.wakeup.wait(), timeout=5)
        finally:
            hb.cancel()
            startup_task.cancel()
            for t in self.tasks.values():
                t.cancel()
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            if self._listener:
                await self._listener.close()

    def stop(self) -> None:
        self.stopping = True
        self.wakeup.set()

    async def _listen(self) -> None:
        dsn = config.database_url().replace("postgresql+asyncpg://", "postgresql://")
        try:
            self._listener = await asyncpg.connect(dsn)
            await self._listener.add_listener(CHANNEL, self._on_notify)
        except Exception as e:  # polling still works
            log.warning("LISTEN failed (%s); falling back to polling", e)

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        if payload.startswith("cancel:"):
            with contextlib.suppress(ValueError):
                jid = int(payload.split(":", 1)[1])
                asyncio.get_running_loop().create_task(self._check_cancel(jid))
        self.wakeup.set()

    # ----------------------------------------------------------------- claiming

    async def _fill(self) -> None:
        while len(self.tasks) < MAX_CONCURRENT and not self.stopping:
            async with SessionLocal() as db:
                job_id = (await db.execute(CLAIM_SQL)).scalar_one_or_none()
                await db.commit()
            if job_id is None:
                return
            self.tasks[job_id] = asyncio.create_task(self._run_job(job_id))

    async def _run_job(self, job_id: int) -> None:
        try:
            async with SessionLocal() as db:
                job = await db.get(Job, job_id)
                assert job is not None
                ctx = JobContext(job)
            fn = HANDLERS.get(ctx.kind)
            if fn is None:
                await self._finish(job_id, "failed", error=f"unknown job kind {ctx.kind!r}")
                return
            await ctx.log(f"started (attempt {ctx.attempt})")
            try:
                result = await fn(ctx)
            except asyncio.CancelledError:
                if self.stopping:
                    await self._requeue(job_id, "worker shutting down; job re-queued")
                else:
                    await self._finish(job_id, "cancelled", error="cancelled")
                raise
            except JobFailed as e:
                await self._finish(job_id, "failed", error=str(e))
                return
            except Exception as e:
                from ..egress.gate import GateRefused

                if isinstance(e, GateRefused):
                    await self._finish(job_id, "failed", error=str(e))
                    return
                log.exception("job %s failed", job_id)
                await self._finish(job_id, "failed", error=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-4000:]}")
                return
            await self._finish(job_id, "succeeded", result=result or {})
        except asyncio.CancelledError:
            pass
        finally:
            self.tasks.pop(job_id, None)
            self.wakeup.set()

    async def _finish(self, job_id: int, status: str, *, result: dict | None = None, error: str | None = None) -> None:
        async with SessionLocal() as db:
            values = {"status": status, "finished_at": datetime.now(UTC), "updated_at": datetime.now(UTC)}
            if status == "succeeded":
                values["progress"] = 1.0
            if result is not None:
                values["result"] = result
            if error is not None:
                values["error"] = error
            await db.execute(update(Job).where(Job.id == job_id).values(**values))
            msg = status if not error else f"{status}: {error.splitlines()[0][:500]}"
            job = await db.get(Job, job_id)
            context = {"stage": "job", "at": job.progress_text} if job is not None and job.progress_text and status != "succeeded" else {"stage": "job"}
            db.add(JobLog(job_id=job_id, level="error" if status == "failed" else "info", message=msg, context=context))
            await db.commit()

    async def _requeue(self, job_id: int, reason: str) -> None:
        async with SessionLocal() as db:
            await db.execute(update(Job).where(Job.id == job_id).values(status="queued"))
            db.add(JobLog(job_id=job_id, level="warn", message=reason))
            await db.commit()

    # ----------------------------------------------------------------- heartbeat / cancel / recovery

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                ALIVE_FILE.write_text(str(time.time()))
                if self.tasks:
                    async with SessionLocal() as db:
                        await db.execute(
                            update(Job).where(Job.id.in_(list(self.tasks))).values(heartbeat_at=datetime.now(UTC))
                        )
                        await db.commit()
                    for jid in list(self.tasks):
                        await self._check_cancel(jid)
                await self.recover_stale()
            except Exception:
                log.exception("heartbeat failed")
            await asyncio.sleep(HEARTBEAT_S)

    async def _check_cancel(self, job_id: int) -> None:
        task = self.tasks.get(job_id)
        if task is None:
            return
        async with SessionLocal() as db:
            flag = (await db.execute(select(Job.cancel_requested).where(Job.id == job_id))).scalar_one_or_none()
        if flag:
            task.cancel()

    async def recover_stale(self, all_running: bool = False) -> None:
        async with SessionLocal() as db:
            q = select(Job).where(Job.status == "running")
            if not all_running:
                q = q.where(Job.heartbeat_at < datetime.now(UTC) - STALE_AFTER)
            for job in (await db.execute(q.with_for_update(skip_locked=True))).scalars():
                if job.id in self.tasks:
                    continue
                if job.cancel_requested:
                    job.status = "cancelled"
                    job.finished_at = datetime.now(UTC)
                else:
                    job.status = "queued"
                db.add(JobLog(job_id=job.id, level="warn", message="recovered after worker restart"))
            await db.commit()


def main() -> None:
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Background work yields the CPU to interactive requests (backend, compiles).
    with contextlib.suppress(OSError):
        os.nice(10)
    from .. import appsecrets

    appsecrets.load()
    worker = Worker()

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, worker.stop)
        await worker.run()

    asyncio.run(_main())


if __name__ == "__main__":
    main()
