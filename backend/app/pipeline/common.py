"""Helpers shared by the pipelines: memoised AI calls, scratch dirs."""

from __future__ import annotations

import asyncio
import random
import shutil
from pathlib import Path

from ..config import config
from ..egress import gate
from ..egress.schemas import EgressRequest, EgressResult
from ..worker.context import JobContext, JobFailed


def job_dir(job_id: int, name: str) -> Path:
    d = config.latex_work_dir / "jobs" / str(job_id) / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup_job_dirs(job_id: int) -> None:
    shutil.rmtree(config.latex_work_dir / "jobs" / str(job_id), ignore_errors=True)


async def ai(ctx: JobContext, req: EgressRequest, *, title: str, retries: int = 1) -> EgressResult:
    """Send once per request key (results are memoised, so a retried job never
    re-sends what already succeeded). Transient failures are retried."""
    key = "ai:" + req.request_key
    done, res = await ctx.get_step(key)
    if done:
        return EgressResult.model_validate(res)
    last: gate.GateSendError | None = None
    for attempt in range(retries + 1):
        if attempt and last is not None:
            # Back off before retrying; rate limits need longer.
            base = 6.0 if last.kind == "rate_limit" else 2.0
            await asyncio.sleep(base * attempt + random.uniform(0, base))
        try:
            result = await gate.send(req, gate.GateContext(job_id=ctx.job_id, course_id=ctx.course_id, title=title))
            await ctx.set_step(key, result.model_dump(mode="json"))
            return result
        except gate.GateSendError as e:
            last = e
            final = attempt >= retries or e.kind in NO_RETRY
            await ctx.log(
                f"{title}: {req.task} attempt {attempt + 1}/{retries + 1} failed [{e.kind}]: {e}",
                "error" if final else "warn",
                stage=req.task.split(".")[0], task=req.task, item=title, request_key=req.request_key, attempt=attempt + 1,
                kind=e.kind, audit_id=e.audit_id, provider=e.provider, model=e.model,
            )
            if e.kind in NO_RETRY:
                break
    if last is not None and last.kind == "truncated":
        raise ReplyTruncated(f"{req.task} for {title}: {last}")
    raise JobFailed(f"{req.task} failed for {title} [{last.kind if last else '?'}]: {last}")


class ReplyTruncated(JobFailed):
    """The reply hit the output limit: the caller can split the work and try again."""


# Retrying these can't help.
NO_RETRY = {"auth", "quota", "bad_request", "refused", "truncated"}
