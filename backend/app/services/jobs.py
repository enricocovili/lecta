"""Job queue API used by the backend (enqueue / cancel / retry / delete).

The queue is the `jobs` table; the worker claims rows with
`SELECT ... FOR UPDATE SKIP LOCKED` and is woken up via LISTEN/NOTIFY.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import notify
from ..models import IngestFigure, IngestItem, Job, JobLog, JobStep, SourceFile, SourceLink, Upload

CHANNEL = "lecta_jobs"
ACTIVE = ("queued", "running")
FINISHED = ("succeeded", "failed", "cancelled")

# Lower value = picked first.
PRIORITY = {"interactive": 1, "ingest": 5, "publish": 3, "index": 8, "maintenance": 9}


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    title: str = "",
    priority: int | str = "ingest",
    course_id: int | None = None,
    parent_id: int | None = None,
    commit: bool = True,
) -> Job:
    prio = PRIORITY.get(priority, 5) if isinstance(priority, str) else priority
    job = Job(
        kind=kind, title=title or kind, payload=payload or {}, priority=prio, course_id=course_id, parent_id=parent_id
    )
    db.add(job)
    await db.flush()
    db.add(JobLog(job_id=job.id, level="info", message=f"queued ({kind})"))
    await notify(db, CHANNEL, str(job.id))
    if commit:
        await db.commit()
    return job


async def request_cancel(db: AsyncSession, job: Job) -> None:
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = datetime.now(UTC)
        db.add(JobLog(job_id=job.id, level="warn", message="cancelled"))
    elif job.status == "running":
        job.cancel_requested = True
        db.add(JobLog(job_id=job.id, level="warn", message="cancellation requested"))
    await notify(db, CHANNEL, f"cancel:{job.id}")
    await db.commit()


async def retry(db: AsyncSession, job: Job, *, from_scratch: bool = False) -> None:
    if job.status in ("running",):
        return
    if from_scratch:
        # Steps that wrote into a course stay memoised: redoing them would add the material twice.
        await db.execute(delete(JobStep).where(JobStep.job_id == job.id, JobStep.key.not_like("%:apply:%")))
    job.status = "queued"
    job.error = None
    job.cancel_requested = False
    job.finished_at = None
    db.add(JobLog(job_id=job.id, level="info", message="retry" + (" from scratch" if from_scratch else "")))
    await notify(db, CHANNEL, str(job.id))
    await db.commit()


async def active_count(db: AsyncSession) -> int:
    from sqlalchemy import func

    return (await db.execute(select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE)))).scalar_one()


async def delete_job(db: AsyncSession, job: Job, *, purge: bool = False) -> dict[str, int]:
    """Delete a finished job with its logs, steps and scratch data.

    With `purge` the job's upload (with its source files) goes too, when nothing
    written into a course came from it.
    """
    from ..pipeline.common import cleanup_job_dirs

    job_id = job.id
    out = {"uploads": 0}
    await db.execute(delete(IngestFigure).where(IngestFigure.job_id == job_id))
    await db.execute(delete(IngestItem).where(IngestItem.job_id == job_id))
    upload_ids = list((await db.execute(select(Upload.id).where(Upload.job_id == job_id))).scalars())
    removed: list[int] = []
    if purge:
        for uid in upload_ids:
            linked = (
                await db.execute(
                    select(SourceLink.id).join(SourceFile, SourceFile.id == SourceLink.source_file_id).where(SourceFile.upload_id == uid).limit(1)
                )
            ).first()
            if not linked:
                await db.execute(delete(Upload).where(Upload.id == uid))  # source files cascade
                removed.append(uid)
    await db.execute(update(Upload).where(Upload.job_id == job_id).values(job_id=None))
    await db.execute(delete(Job).where(Job.id == job_id))  # logs and steps cascade
    await db.commit()
    cleanup_job_dirs(job_id)
    for uid in removed:
        _remove_upload_dir(uid)
    out["uploads"] = len(removed)
    return out


def _remove_upload_dir(upload_id: int) -> None:
    import shutil

    from ..config import config

    shutil.rmtree(config.data_dir / "uploads" / str(upload_id), ignore_errors=True)
