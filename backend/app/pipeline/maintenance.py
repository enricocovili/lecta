"""Periodic housekeeping run by the worker."""

from __future__ import annotations

import asyncio
import logging
import shutil

from ..config import config

log = logging.getLogger("lecta.maintenance")


def remove_audit_copies() -> None:
    """Older versions kept copies of every request under data/audit; they are no longer used."""
    d = config.data_dir / "audit"
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
        log.info("removed the old audit payload copies")


async def housekeeping() -> None:
    """Hourly clean-up."""
    from .publish import prune_auto_publish_jobs

    n = await prune_auto_publish_jobs()
    if n:
        log.info("removed %s old automatic publish job(s)", n)


async def loop() -> None:
    await asyncio.to_thread(remove_audit_copies)
    while True:
        try:
            await housekeeping()
        except Exception:
            log.exception("maintenance failed")
        await asyncio.sleep(3600)
