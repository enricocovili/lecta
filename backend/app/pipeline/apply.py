"""Writing AI output into a course: new chapters, appends, images, source links.

Imports write straight into the chapters (no review step). Every write happens in
one transaction together with the job step that records it, so a job that is
resumed or retried never adds the same material twice. Chat edits use the same
search/replace helpers.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import Chapter, Course, JobStep, SourceLink
from ..services import blobs, compile as compile_svc, latex, latexmacros, projects, templates, texlog
from ..services.texttools import slugify
from ..worker.context import JobContext
from .common import ai, job_dir
from .requests import RequestBuilder

log = logging.getLogger("lecta.apply")

_DROP_LINE_RE = re.compile(
    r"\\(documentclass|usepackage|RequirePackage|begin\{document\}|end\{document\}|input|include|includeonly|write18|openout|openin|immediate\\write)\b"
)


class EditError(Exception):
    pass


def apply_edits(text: str, edits: list[dict[str, Any]]) -> str:
    """Apply search/replace edits; every search string must occur exactly once."""
    out = text
    for e in edits:
        search, replace = e.get("search"), e.get("replace")
        if not isinstance(search, str) or not isinstance(replace, str) or not search:
            raise EditError("malformed edit")
        count = out.count(search)
        if count != 1:
            raise EditError(f"search text found {count} times: {search[:80]!r}")
        out = out.replace(search, replace, 1)
    return out


def validate_edit_path(path: str) -> str:
    p = projects.validate_path(path)
    if not projects.is_text_path(p):
        raise EditError(f"{p}: only text files can be edited")
    return p


def sanitize_body(body: str) -> str:
    """Model LaTeX is untrusted: keep it a chapter body (no preamble, no file access)."""
    body = body.strip()
    m = re.match(r"^```(?:latex|tex)?\s*(.*?)```$", body, re.S)
    if m:
        body = m.group(1)
    lines = []
    for ln in body.split("\n"):
        if _DROP_LINE_RE.search(ln):
            lines.append("% (removed by Lecta) " + ln.replace("\\", "\\textbackslash "))
            continue
        lines.append(re.sub(r"\\chapter\*?\s*\{", r"\\section{", ln))
    return "\n".join(lines).strip() + "\n"


def clean_model_text(text: str) -> str:
    return sanitize_body(text)


def assemble_chapter(title: str, slug: str, body: str) -> str:
    return f"\\chapter{{{templates.tex_escape(title)}}}\\label{{ch:{slug}}}\n\n{body.strip()}\n"


# --------------------------------------------------------------------------- a lesson's section of a chapter
#
# The text written from a lesson sits between two marker comments. They tie the chapter to the lesson: generating the
# lesson again finds its section and rewrites it there, leaving everything around it (other lessons, the student's own
# additions before or after) where it is.


def _lesson_begin(lesson_id: int) -> str:
    return rf"^% ==== Lecta: lezione {lesson_id}\b[^\n]*\n"


def _lesson_end(lesson_id: int) -> str:
    return rf"^% ==== Lecta: fine lezione {lesson_id}[ \t]*$"


def lesson_section(text: str, lesson_id: int) -> str | None:
    """The text a lesson has in a chapter (between its markers), or None when it has none."""
    m = re.search(_lesson_begin(lesson_id) + r"(.*?)\n?" + _lesson_end(lesson_id), text, re.S | re.M)
    return m.group(1).strip("\n") if m else None


def wrap_lesson_section(lesson_id: int, title: str, body: str, stamp: str) -> str:
    title = re.sub(r"\s+", " ", title)[:200]
    return f"% ==== Lecta: lezione {lesson_id} «{title}» ({stamp})\n{body.strip()}\n% ==== Lecta: fine lezione {lesson_id}\n"


def put_lesson_section(text: str, lesson_id: int, title: str, body: str, stamp: str) -> tuple[str, bool]:
    """The chapter with the lesson's section replaced by `body` (or added at the end when it had none). -> (text, replaced)"""
    block = wrap_lesson_section(lesson_id, title, body, stamp)
    pat = re.compile(_lesson_begin(lesson_id) + r".*?\n?" + _lesson_end(lesson_id) + r"\n?", re.S | re.M)
    if pat.search(text):
        return pat.sub(lambda _m: block, text, count=1), True
    return text.rstrip() + "\n\n" + block, False


def recompile_later(course_id: int, file: str | None) -> None:
    """Recompile the draft in the background after a change."""

    async def run() -> None:
        try:
            async with SessionLocal() as db:
                course = await db.get(Course, course_id)
                if course:
                    await compile_svc.run_build(db, course, file=file, priority="background")
        except Exception:
            log.exception("background recompile failed")

    asyncio.get_running_loop().create_task(run())


# --------------------------------------------------------------------------- compile check


def temp_image_path(key: str, ext: str) -> str:
    return f"images/{key}.{ext}"


def with_image_paths(body: str, images: dict[str, dict[str, Any]]) -> str:
    """The picture keys of \\lectaimage, \\lectaimagewithtext and \\lectaimagepair → the paths of this job's pictures."""

    def path(key: str) -> str | None:
        img = images.get(key)
        return temp_image_path(key, img["ext"]) if img else None

    return latexmacros.map_images(body, path)


async def check_and_fix(
    ctx: JobContext, group_key: str, title: str, body: str, images: dict[str, dict[str, Any]], *,
    preamble: str, engine: str, language: str, autofix: int, timeout: int, extra_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compile the new chapter in a scratch project; on errors in it, at most `autofix` AI fixes.
    `body` uses temporary image paths (images/KEY.ext). Nothing is written to the course here."""
    slug = "notes"
    header_lines = 2  # \chapter line + blank line
    diagnostics: list[dict[str, Any]] = []
    status = "error"
    files = {**(extra_files or {}), **{temp_image_path(k, v["ext"]): "blob:" + v["blob"] for k, v in images.items()}}
    for attempt in range(autofix + 1):
        wd = job_dir(ctx.job_id, f"g-{group_key}")
        project = {
            "main.tex": templates.main_tex(title, None, language, [f"chapters/01-{slug}.tex"]),
            "preamble.tex": templates.with_compat(preamble),
            f"chapters/01-{slug}.tex": assemble_chapter(title, slug, body),
            **files,
        }
        projects.materialize_files(wd, project)
        res = await latex.compile(
            {
                "key": f"job-{ctx.job_id}-{group_key}",
                "workdir": latex.rel(wd),
                "figure_cache": latex.rel(job_dir(ctx.job_id, "figcache")),
                "engine": engine,
                "mode": "draft",
                "priority": "background",
                "timeout": timeout,
            }
        )
        diagnostics = compile_svc.figure_diagnostics(res.get("figures", [])) + texlog.parse(res.get("log", ""))
        status = res.get("status", "error")
        errors = [d for d in diagnostics if d["level"] == "error" and (d.get("file") or "").startswith("chapters/")]
        if status == "ok" or not errors or attempt >= autofix:
            break
        await ctx.log(f"compile errors in “{title}”: " + "; ".join(e["message"] for e in errors[:3]), "warn",
                      stage="compile", item=title, kind="compile_error", attempt=attempt + 1)
        error_lines = sorted({e["line"] - header_lines for e in errors if e.get("line") and e["line"] > header_lines})
        async with SessionLocal() as db:
            rb = RequestBuilder("notes.fix")
            rb.data(body, "the LaTeX chapter body")
            rb.data(
                "\n".join(f"line {e['line'] - header_lines if e.get('line') else '?'}: {e['message']}"
                          + (f" (near: {e['context']})" if e.get("context") else "") for e in errors[:15]),
                "compile errors",
            )
            req = await rb.build(db, role="writing", task="notes.fix", request_key=f"{ctx.job_id}:fix:{group_key}:{attempt}",
                                 json_output=False, temperature=0.1, meta={"latex": body, "error_lines": error_lines})
        fixed = await ai(ctx, req, title=f"Correzione automatica: {title}")
        new_body = sanitize_body(fixed.text.replace("%%END", "").strip()) if fixed.text.strip() else body
        if new_body.strip() == body.strip():
            break
        body = new_body
    return {"status": status, "diagnostics": diagnostics[:200], "body": body}


# --------------------------------------------------------------------------- writing into a course


async def _allocate_image_paths(db: AsyncSession, course_id: int, base: str, exts: list[str]) -> list[str]:
    existing = set((await projects.manifest(db, course_id)).keys())
    base = slugify(base, 30) or "img"
    out, n = [], 1
    for ext in exts:
        while any(f"images/{base}-img{n}.{e}" in existing for e in ("png", "jpg", "jpeg")):
            n += 1
        out.append(f"images/{base}-img{n}.{ext}")
        n += 1
    return out


async def write_group(ctx: JobContext, key: str, target: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    """Write one group's notes into a course, exactly once per (job, key).

    target: {"type": "new_chapter", course_id, title} | {"type": "append", course_id, chapter_id};
            plus "lesson": {id, title} when the text comes from a lesson
            (it is written between that lesson's markers, and an append replaces the lesson's earlier section)
    bundle: {title, body (images as images/KEY.ext), images: {KEY: {blob, ext}}, source_file_ids, label}
    """
    done, result = await ctx.get_step(key)
    if done:
        return result
    async with SessionLocal() as db:
        course = (await db.execute(select(Course).where(Course.id == int(target["course_id"])).with_for_update())).scalar_one()
        chapter = None
        if target["type"] == "append":
            chapter = await db.get(Chapter, int(target["chapter_id"]))
            if chapter is None or chapter.course_id != course.id:
                chapter = None
        base = chapter.slug if chapter else (target.get("title") or bundle["title"])
        keys = [k for k in bundle.get("images", {}) if temp_image_path(k, bundle["images"][k]["ext"]) in bundle["body"]]
        paths = await _allocate_image_paths(db, course.id, base, [bundle["images"][k]["ext"] for k in keys])
        body = bundle["body"]
        for k, path in zip(keys, paths, strict=True):
            img = bundle["images"][k]
            body = body.replace("{" + temp_image_path(k, img["ext"]) + "}", "{" + path + "}")
            await projects.write_file(db, course, path, blobs.read_bytes(img["blob"]), meta={"origin": "source"}, validated=True)
        lesson = target.get("lesson")
        stamp = datetime.now(UTC).strftime("%d/%m/%Y")
        replaced = False
        if chapter is not None:
            existing = await projects.read_text(db, course.id, chapter.path) or ""
            if lesson:
                new, replaced = put_lesson_section(existing, int(lesson["id"]), lesson["title"], body, stamp)
            else:
                label = (bundle.get("label") or bundle["title"]).replace("\n", " ")[:200]
                new = existing.rstrip() + f"\n\n% ---- Lecta: aggiunto da «{label}» ({stamp})\n\n" + body.strip() + "\n"
            await projects.write_file(db, course, chapter.path, new.encode(), validated=True)
            kind = "append"
        else:
            title = (target.get("title") or bundle["title"])[:300]
            ch_slug = slugify(title, 40)
            if lesson:
                body = wrap_lesson_section(int(lesson["id"]), lesson["title"], body, stamp)
            chapter = await projects.add_chapter(db, course, title, content=assemble_chapter(title, ch_slug, body))
            # add_chapter may pick a different slug when taken: keep the \label in sync.
            if chapter.slug != ch_slug:
                await projects.write_file(db, course, chapter.path, assemble_chapter(title, chapter.slug, body).encode(), validated=True)
            kind = "new_chapter"
        await db.flush()
        for sid in bundle.get("source_file_ids") or []:
            exists = (await db.execute(select(SourceLink.id).where(
                SourceLink.source_file_id == int(sid), SourceLink.course_id == course.id, SourceLink.chapter_id == chapter.id))).first()
            if not exists:
                db.add(SourceLink(source_file_id=int(sid), course_id=course.id, chapter_id=chapter.id, job_id=ctx.job_id))
        result = {"type": kind, "course_id": course.id, "course_name": course.name, "chapter_id": chapter.id,
                  "chapter_title": chapter.title, "path": chapter.path, "images": paths, "image_paths": dict(zip(keys, paths, strict=True)),
                  "title": bundle["title"], "replaced": replaced}
        await db.execute(insert(JobStep).values(job_id=ctx.job_id, key=key, result=result).on_conflict_do_nothing())
        await db.commit()
    recompile_later(result["course_id"], result["path"])
    return result
