"""Tasks started once when the worker starts."""

from __future__ import annotations

import asyncio


async def on_worker_start(worker) -> None:  # noqa: ANN001
    from . import indexer, maintenance, publish

    tasks = [asyncio.create_task(maintenance.loop()), asyncio.create_task(indexer.loop()), asyncio.create_task(publish.republish_loop())]
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
