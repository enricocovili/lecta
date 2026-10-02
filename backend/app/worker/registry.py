"""Job kind → handler. Handlers are `async def handler(ctx: JobContext) -> dict | None`."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .context import JobContext

Handler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


@handler("demo.sleep")
async def demo_sleep(ctx: JobContext) -> dict[str, Any]:
    """Diagnostic job used by the test-suite: progress, logs, cancel, resume."""
    steps = int(ctx.payload.get("steps", 3))
    delay = float(ctx.payload.get("delay", 0.2))
    done = 0
    for i in range(steps):

        async def one(i: int = i) -> int:
            await asyncio.sleep(delay)
            return i

        await ctx.step(f"sleep-{i}", one)
        done += 1
        await ctx.progress(done / steps, f"step {done}/{steps}")
    if ctx.payload.get("fail"):
        raise RuntimeError("requested failure")
    await ctx.log(f"slept {steps} steps")
    return {"steps": steps}


def load_all() -> None:
    """Import every module that registers handlers."""
    from ..pipeline import jobs as _pipeline_jobs  # noqa: F401
