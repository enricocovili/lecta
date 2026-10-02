"""Da smistare: write an item into the course/chapter I chose (or into a new course)."""

from __future__ import annotations

from typing import Any

from ..db import SessionLocal
from ..models import InboxItem
from ..worker.context import JobContext, JobFailed
from ..worker.registry import handler
from . import placement
from .ingest import place_bundle


@handler("inbox.assign")
async def assign(ctx: JobContext) -> dict[str, Any]:
    inbox_id = int(ctx.payload["inbox_id"])
    course_id = ctx.payload.get("course_id")
    chapter_id = ctx.payload.get("chapter_id")
    async with SessionLocal() as db:
        item = await db.get(InboxItem, inbox_id)
        if item is None or item.status not in ("open", "assigning"):
            raise JobFailed("inbox item is not open")
        bundle = dict(item.bundle)
    # A new job: its request keys are distinct from the original import's.
    bundle["group_key"] = f"inbox{inbox_id}"
    new_title = ctx.payload.get("new_chapter_title")
    if ctx.payload.get("new_course"):
        outcome: dict[str, Any] = {"type": "new_course", "name": ctx.payload["new_course"]["name"],
                                   "language": ctx.payload["new_course"].get("language") or bundle.get("language") or "it",
                                   "title": new_title or bundle["title"]}
    else:
        outcome = await ctx.step(
            "place", lambda: placement.decide(ctx, bundle, target_course_id=int(course_id) if course_id else None,
                                              target_chapter_id=int(chapter_id) if chapter_id else None)
        )
        if outcome["type"] == "inbox":
            outcome = {"type": "new_chapter", "course_id": int(course_id), "title": bundle["title"], "confidence": 1.0,
                       "rationale": "assigned by you from Da smistare"}
        if new_title and outcome["type"] == "new_chapter":
            outcome["title"] = new_title
    result = await place_bundle(ctx, f"inbox{inbox_id}", bundle, outcome)
    async with SessionLocal() as db:
        item = await db.get(InboxItem, inbox_id)
        item.status = "assigned"
        await db.commit()
    return {"groups": [{"group": bundle["title"], **result}]}
