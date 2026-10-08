"""The lesson assistant's tools: it only reads. The whole lesson (every page's slide text, typed notes and whether it carries
handwriting) is in its prompt and the pictures of the pages around the one asked about come with the question
(lesson_agent.py); with these it looks at any other page and reads the study text written from the lesson.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from ..db import SessionLocal
from ..egress.schemas import ToolSpec
from ..models import Lesson, LessonPage
from ..services import blobs
from ..services import lessons as ls
from . import lab_tools
from .agent_tools import ToolError, ToolResult, _clip, _int, _obj

_I = {"type": "integer"}

# The asked page is seen at the size the import reads handwriting; the pages around it smaller (still legible).
ASKED_SIDE = ls.RENDER_SIDE
NEAR_SIDE = 1100
# Pages on each side of the asked one that come with the question as pictures.
WINDOW = 10
MAX_SLIDE_CHARS = 3000
MAX_NOTES_CHARS = 6000
MAX_OUTLINE_CHARS = 200_000


@dataclass
class LessonContext:
    lesson_id: int
    course_id: int
    nonce: str


SPECS: list[ToolSpec] = [
    ToolSpec(name="view_page", description="Look at a page of the lesson as a picture, with the student's handwriting and highlights on it. "
                                           "`page` is the page's number in the outline ([p. N]). The pictures of the pages near the one asked about are already in the question.",
             parameters=_obj({"page": _I}, ["page"])),
    ToolSpec(name="read_chapter", description="The study text (LaTeX) written from this lesson, in the course's chapter: the lesson's own section when it has one. "
                                              "Use start_line/end_line for long chapters.",
             parameters=_obj({"start_line": _I, "end_line": _I})),
]


def label_for(name: str, args: dict[str, Any]) -> str:
    return {
        "view_page": f"Guarda la pagina {args.get('page')}",
        "read_chapter": "Legge il capitolo della lezione",
    }.get(name, name)


def fence(ctx: LessonContext, text: str, label: str) -> str:
    from ..services import prompts

    return prompts.fence(text, ctx.nonce, label)


def page_dicts(pages: list[LessonPage]) -> list[dict[str, Any]]:
    return [{"id": p.id, "kind": p.kind, "slide_page": p.slide_page, "ratio": p.ratio, "notes": p.notes, "ink": p.ink} for p in pages]


async def load(lesson_id: int) -> tuple[Lesson, list[dict[str, Any]]]:
    async with SessionLocal() as db:
        lesson = await db.get(Lesson, lesson_id)
        rows = (await db.execute(select(LessonPage).where(LessonPage.lesson_id == lesson_id).order_by(LessonPage.position))).scalars().all()
        return lesson, page_dicts(list(rows))


def pdf_path(lesson: Lesson):
    return blobs.path_for(lesson.pdf_blob) if lesson.pdf_blob and blobs.exists(lesson.pdf_blob) else None


def tag(pages: list[dict[str, Any]], index: int) -> str:
    """How a page is named to the model: its number in the lesson and its label («[p. 7] Slide 5»)."""
    return f"[p. {index + 1}] {ls.page_label(pages, index)}"


async def outline(ctx: LessonContext, lesson: Lesson, pages: list[dict[str, Any]]) -> str:
    """Every page of the lesson: the text printed on the slide, the student's typed notes, and whether it carries handwriting."""
    path = pdf_path(lesson)
    texts = await asyncio.to_thread(ls.slide_texts, path, [p["slide_page"] for p in pages if p["kind"] == "slide" and p["slide_page"]]) if path else {}
    out: list[str] = []
    for i, p in enumerate(pages):
        bits = [tag(pages, i) + (" · handwriting on it: yes" if p["ink"] else "")]
        if p["kind"] == "slide":
            text = (texts.get(p["slide_page"] or 0) or "").strip()
            bits.append("Slide text:\n" + (_clip(text, MAX_SLIDE_CHARS) if text else "(no text layer: look at the picture)"))
        notes = (p["notes"] or "").strip()
        bits.append("Student's notes:\n" + (_clip(notes, MAX_NOTES_CHARS) if notes else "(none)"))
        out.append("\n".join(bits))
    return fence(ctx, _clip("\n\n".join(out), MAX_OUTLINE_CHARS), f"lesson «{lesson.title}», {len(pages)} pages")


def _jpeg(im: Any) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


async def pictures(lesson: Lesson, pages: list[dict[str, Any]], indexes: list[int], sides: list[int]) -> list[dict[str, Any]]:
    """The pictures of some pages (with their strokes), stored in the blob store: [{blob, mime, width, height, label}]."""
    ims = await asyncio.to_thread(ls.render_pages, pdf_path(lesson), [pages[i] for i in indexes], sides)
    return [{"blob": blobs.put_bytes(_jpeg(im)), "mime": "image/jpeg", "width": im.width, "height": im.height, "label": tag(pages, i)}
            for i, im in zip(indexes, ims, strict=True)]


async def view_page(ctx: LessonContext, args: dict[str, Any]) -> ToolResult:
    lesson, pages = await load(ctx.lesson_id)
    n = _int(args, "page")
    if n is None or not 1 <= n <= len(pages):
        raise ToolError(f"the lesson has pages 1–{len(pages)}")
    img = (await pictures(lesson, pages, [n - 1], [ASKED_SIDE]))[0]
    return ToolResult(f"Here is {tag(pages, n - 1)} (picture attached).", summary=ls.page_label(pages, n - 1), images=[img])


async def read_chapter(ctx: LessonContext, args: dict[str, Any]) -> ToolResult:
    return await lab_tools.read_chapter(ctx, args)  # type: ignore[arg-type]  # it needs lesson_id, course_id and nonce


_TOOLS = {"view_page": view_page, "read_chapter": read_chapter}


async def run_tool(ctx: LessonContext, name: str, args: dict[str, Any]) -> ToolResult:
    fn = _TOOLS.get(name)
    if fn is None:
        return ToolResult(f"Error: unknown tool {name}", "strumento sconosciuto", ok=False)
    try:
        res = await fn(ctx, args if isinstance(args, dict) else {})
    except ToolError as e:
        return ToolResult(f"Error: {e}", str(e)[:160], ok=False)
    res.text = _clip(res.text)
    return res
