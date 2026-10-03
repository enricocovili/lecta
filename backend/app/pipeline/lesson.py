"""A lesson as the input of the import.

Generating the text of a lesson hands the import (pipeline/ingest.py) the same things an upload has, made from a
snapshot of the lesson taken when the button was pressed:

* the slides PDF, with the slides that carry handwriting replaced by a picture of the slide *with* the strokes
  (so the reading model sees what was underlined, circled or written in the margin);
* the typed notes as one Markdown file, a section per slide («Slide 5»), which is the backbone of the text;
* every blank page written on by hand, as a picture (read like a photo of handwritten notes).

Nothing else changes: grouping, reading, writing, placement and the compile check are the import's own.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Any

from PIL import Image
from sqlalchemy import delete, select

from ..db import SessionLocal
from ..models import Lesson, SourceFile, Upload
from ..services import blobs
from ..services import lessons as ls
from ..services.texttools import slugify
from ..worker.context import JobContext, run_cpu
from .analyze import _thumb


def _jpeg(im: Image.Image, q: int = 85) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q)
    return buf.getvalue()


def _render_all(snap: dict[str, Any]) -> dict[str, Any]:
    """CPU work: the pictures of the pages that carry strokes. Returns blobs and sizes (JSON-able)."""
    pages = snap["pages"]
    pdf_path = blobs.path_for(snap["pdf_blob"]) if snap.get("pdf_blob") and blobs.exists(snap["pdf_blob"]) else None
    annotated: dict[str, dict[str, Any]] = {}
    handwritten: list[dict[str, Any]] = []
    for i, p in enumerate(pages):
        ink = p.get("ink") or []
        if not ink:
            continue
        if p["kind"] == "slide" and pdf_path is not None:
            im = ls.render_slide(pdf_path, p["slide_page"], ink)
            annotated[str(p["slide_page"])] = {"blob": blobs.put_bytes(_jpeg(im)), "width": im.width, "height": im.height}
        else:
            im = ls.render_blank(p.get("ratio") or 0.75, ink)
            handwritten.append({"position": p["position"], "index": i, "blob": blobs.put_bytes(_jpeg(im)), "preview": _thumb(im),
                                "width": im.width, "height": im.height})
    return {"annotated": annotated, "handwritten": handwritten}


async def prepare(ctx: JobContext, upload_id: int, snapshot_blob: str) -> dict[str, Any]:
    """Create the upload's files from the lesson snapshot. Idempotent (a retry replaces what it made)."""
    snap = json.loads(blobs.read_text(snapshot_blob))
    pages: list[dict[str, Any]] = snap["pages"]
    title: str = snap.get("title") or "Lezione"
    rendered = await run_cpu(_render_all, snap)
    # Slides deleted from the lesson are not part of the text: the PDF handed to the import has only the remaining ones,
    # numbered 1, 2, 3… (as the notes' «Slide N» headings and the reading step's `% slide N` markers are).
    pdf_blob, pdf_pages = snap.get("pdf_blob"), snap.get("pdf_pages") or 0
    numbered, keep = ls.renumber_slides(pages)
    if pdf_blob and keep != list(range(1, pdf_pages + 1)):
        if keep:
            data = await run_cpu(ls.subset_pdf, blobs.path_for(pdf_blob), keep)
            pdf_blob, pdf_pages = blobs.put_bytes(data), len(keep)
        else:
            pdf_blob, pdf_pages = None, 0
        rendered["annotated"] = {str(numbered_p["slide_page"]): rendered["annotated"][str(orig)]
                                 for orig, numbered_p in zip(keep, [q for q in numbered if q["kind"] == "slide"]) if str(orig) in rendered["annotated"]}
    pages = numbered
    lesson_meta = {"id": snap.get("lesson_id"), "title": title}
    md = ls.notes_markdown(title, pages)

    async with SessionLocal() as db:
        await db.execute(delete(SourceFile).where(SourceFile.upload_id == upload_id, SourceFile.meta["generated"].astext == "true"))
        if pdf_blob:
            db.add(SourceFile(
                upload_id=upload_id, name=snap.get("pdf_name") or "slide.pdf", kind="pdf", mime="application/pdf",
                size=blobs.path_for(pdf_blob).stat().st_size, blob=pdf_blob, pages=pdf_pages or None,
                meta={"generated": True, "lesson": {**lesson_meta, "annotated": rendered["annotated"]}},
            ))
        if md is not None:
            data = md.encode()
            db.add(SourceFile(
                upload_id=upload_id, name=f"{slugify(title, 50)}-appunti.md", kind="markdown", mime="text/markdown", size=len(data),
                blob=blobs.put_bytes(data), meta={"generated": True, "lesson": lesson_meta},
            ))
        for h in rendered["handwritten"]:
            label = ls.page_label(pages, h["index"])
            db.add(SourceFile(
                upload_id=upload_id, name=f"pagina-{h['position']:03d}.jpg", kind="image", mime="image/jpeg",
                size=blobs.path_for(h["blob"]).stat().st_size, blob=h["blob"],
                meta={"generated": True, "lesson": lesson_meta, "lesson_page": h["position"], "label": label,
                      "preview": h["preview"], "width": h["width"], "height": h["height"]},
            ))
        await db.flush()
        up = await db.get(Upload, upload_id)
        if up is not None:
            up.file_count = len((await db.execute(select(SourceFile.id).where(SourceFile.upload_id == upload_id))).all())
        await db.commit()
    summary = {
        "slides": sum(1 for p in pages if p["kind"] == "slide"), "annotated_slides": len(rendered["annotated"]),
        "handwritten_pages": len(rendered["handwritten"]), "notes_pages": sum(1 for p in pages if (p.get("notes") or "").strip()),
    }
    await ctx.log(
        f"lezione «{title}»: {summary['slides']} slide ({summary['annotated_slides']} con scrittura a mano), {summary['handwritten_pages']} pagine "
        f"scritte a mano, appunti su {summary['notes_pages']} pagine", stage="analyze", kind="lesson")
    return summary


async def finish(lesson_id: int, job_id: int, results: list[dict[str, Any]]) -> None:
    """Remember what the latest generation produced, for the lesson's page."""
    async with SessionLocal() as db:
        lesson = (await db.execute(select(Lesson).where(Lesson.id == lesson_id))).scalar_one_or_none()
        if lesson is None:
            return
        now = datetime.now(UTC)
        lesson.generated_at = now
        lesson.last_result = {
            "job_id": job_id, "at": now.isoformat(),
            "chapters": [
                {"type": r.get("type"), "course_id": r.get("course_id"), "chapter_id": r.get("chapter_id"), "title": r.get("chapter_title") or r.get("group")}
                for r in results if r.get("type") in ("new_chapter", "append")
            ],
        }
        # The lesson now lives in the chapter it was written into: generating it again updates the text there.
        written = [r for r in results if r.get("type") in ("new_chapter", "append") and r.get("chapter_id")]
        if written and lesson.course_id == written[0].get("course_id"):
            lesson.chapter_id = int(written[0]["chapter_id"])
        # Merged into the notes: the lesson is done.
        if written:
            lesson.status = "completed"
        await db.commit()
