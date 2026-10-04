"""The import job: a lesson's material through one pipeline, start to finish without questions.

1. Extraction (local)  PDFs → text with math hints + pictures + page routing (pdfextract);
                       the lesson's notes as they are; its pages written by hand as pictures
2. Groups              the lesson is one group: its notes with its slides
3. Reading             every unit (≈10 pages of one file, or one handwritten page) → LaTeX, all in
                       parallel: a faithful conversion of the material, formulas and pictures included
4. Writing             per group, the study text in prose: the class notes are its backbone, the
                       material fills it in (without notes the material is summarised)
5. Per group           placement → compile check (≤ 1 AI fix) → written into the course
                       (a new chapter of the lesson's course, or appended to one of its chapters)

Every step is memoised (job_steps) and every AI call by its request key, so a retried
or restarted job never repeats finished work; writing into a course happens exactly once.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Any

from sqlalchemy import delete, select

from ..db import SessionLocal
from ..models import Chapter, Course, IngestFigure, IngestItem, SourceFile, Upload
from ..services import blobs, latexmacros, projects, templates
from ..services import settings as settings_svc
from ..services.texttools import slugify, strip_nul
from ..worker.context import JobContext, JobFailed, run_cpu
from ..worker.registry import handler
from . import analyze, apply, compose, pdfextract, placement, read
from . import lesson as lesson_pipeline
from .common import cleanup_job_dirs

GENERIC_TITLES = re.compile(r"^(presentazione|presentation|untitled|senza titolo|microsoft (word|powerpoint)|slide ?\d*|diapositiva)", re.I)


def image_ext(blob: str) -> str:
    with open(blobs.path_for(blob), "rb") as f:
        return "jpg" if f.read(3) == b"\xff\xd8\xff" else "png"


# --------------------------------------------------------------------------- 1. extraction


async def extract(ctx: JobContext, upload_id: int) -> dict[str, Any]:
    """Local extraction. Idempotent: redoes everything for this job from scratch."""
    async with SessionLocal() as db:
        await db.execute(delete(IngestFigure).where(IngestFigure.job_id == ctx.job_id))
        await db.execute(delete(IngestItem).where(IngestItem.job_id == ctx.job_id))
        await db.commit()
        files = list((await db.execute(select(SourceFile).where(SourceFile.upload_id == upload_id).order_by(SourceFile.id))).scalars())

    usable = [f for f in files if f.status == "ok" and f.kind in ("pdf", "image", "markdown", "text")]
    usable.sort(key=lambda f: ({"pdf": 0, "markdown": 1, "text": 1, "image": 2}[f.kind], _natural(f.name)))
    items: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []
    unsupported = [f for f in files if f.status != "ok"]
    position = 0

    md_results: dict[int, dict[str, Any]] = {}
    for f in usable:
        if f.kind in ("markdown", "text"):
            try:
                md_results[f.id] = await run_cpu(analyze.analyze_markdown, blobs.read_bytes(f.blob))
            except Exception as e:  # noqa: BLE001
                md_results[f.id] = {"error": str(e)}

    total = max(1, len(usable))
    for n, f in enumerate(usable):
        await ctx.progress(0.03 + 0.12 * n / total, f"extracting {f.name}")
        label = f.name
        base_meta = {"file": f.name, "file_label": label}
        if f.kind == "pdf":
            try:
                res = await run_cpu(pdfextract.extract_pdf, str(blobs.path_for(f.blob)), f"f{n + 1}")
            except Exception as e:  # noqa: BLE001
                await _mark_error(ctx, f, f"could not read the PDF: {e}")
                continue
            pages = res["pages"]
            lesson_meta = (f.meta or {}).get("lesson")
            for pg in pages:
                # A lesson's slide with handwriting on it goes to the reading step as a picture with the strokes.
                ann = ((lesson_meta or {}).get("annotated") or {}).get(str(pg["page"]))
                if ann and blobs.exists(ann["blob"]):
                    pg["route"], pg["why"] = "eyes", [*pg["why"], "handwriting of the student"]
                    pg["render_blob"], pg["width"], pg["height"] = ann["blob"], ann["width"], ann["height"]
                    pg["annotated"] = True
            await _set_pages(f.id, len(pages), _majority([p.get("language") for p in pages]))
            n_skip = sum(1 for p in pages for s in p["skipped"] if s.get("origin") == "image")
            if n_skip:
                await ctx.log(f"{label}: {n_skip} embedded image(s) ignored (logos, header/footer decoration, repeats)", stage="analyze",
                              item=label, kind="images_ignored")
            eyes = [p["page"] for p in pages if p["route"] == "eyes"]
            if eyes:
                await ctx.log(f"{label}: {len(eyes)} page(s) read with their picture (" + ", ".join(
                    sorted({w for p in pages if p["route"] == "eyes" for w in p["why"]})) + ")", stage="analyze", item=label, kind="pages_with_picture")
            for pg in pages:
                position += 1
                key = f"i{position}"
                items.append(
                    {"key": key, "source_file_id": f.id, "page": pg["page"], "kind": {"skip": "skipped", "eyes": "page_image"}.get(pg["route"], "page"),
                     "label": f"{label} · p. {pg['page']}", "text": pg.get("text") or "", "title": pg.get("title"),
                     "image_blob": pg.get("render_blob"), "preview_blob": pg.get("preview_blob"), "width": pg.get("width"), "height": pg.get("height"),
                     "language": pg.get("language"), "position": position,
                     "meta": {**base_meta, "route": pg["route"], "why": pg["why"], "pictures": [p["id"] for p in pg["pictures"]],
                              "skipped_images": pg["skipped"], "math": pg.get("math"), "meta_title": res.get("meta_title"),
                              **({"lesson": True, "lesson_title": lesson_meta.get("title"), "annotated": bool(pg.get("annotated"))} if lesson_meta else {})}}
                )
                for pic in pg["pictures"]:
                    figures.append({"key": pic["id"], "item_key": key, "origin": pic["origin"], "source_ref": f"source:{f.id}:p{pg['page']}",
                                    "crop_blob": pic["blob"], "bbox": pic["bbox"]})
        elif f.kind == "image":
            fmeta = f.meta or {}
            if not fmeta.get("lesson_page"):
                await _mark_error(ctx, f, "not a page of a lesson")
                continue
            # A page of the lesson written on by hand: already a clean picture, nothing to straighten.
            position += 1
            items.append(
                {"key": f"i{position}", "source_file_id": f.id, "page": None, "kind": "handwritten", "label": fmeta.get("label") or label,
                 "text": None, "image_blob": f.blob, "preview_blob": fmeta.get("preview"), "width": fmeta.get("width"),
                 "height": fmeta.get("height"), "language": None, "position": position,
                 "meta": {**base_meta, "ops": [], "lesson": True, "lesson_title": (fmeta.get("lesson") or {}).get("title"),
                          "lesson_page": fmeta["lesson_page"]}}
            )
        else:
            r = md_results.get(f.id) or {}
            if r.get("error"):
                await _mark_error(ctx, f, r["error"])
                continue
            position += 1
            key = f"i{position}"
            latex = r["latex"]
            # The notes as the model reads them: Markdown with [[IMG id]] where the pictures are.
            notes = r.get("stripped") or r["text"]
            for b in r.get("blocks", []):
                placeholder = f"{analyze.PLACEHOLDER}{b['n']}"
                if b["type"] in ("mermaid", "ascii"):
                    code = b["code"].replace("\\end{verbatim}", "\\end {verbatim}")
                    latex = latex.replace(placeholder, f"\\begin{{verbatim}}\n{code}\n\\end{{verbatim}}")
                    notes = notes.replace(placeholder, f"```{b['type']}\n{b['code']}\n```")
                else:
                    latex = latex.replace(placeholder, f"\\review{{Immagine citata negli appunti, non disponibile: {templates.tex_escape(b.get('path', ''))}}}")
                    notes = notes.replace(placeholder, f"(immagine citata negli appunti, non disponibile: {b.get('path', '')})")
            await _set_pages(f.id, None, r.get("language"))
            items.append(
                {"key": key, "source_file_id": f.id, "page": None, "kind": "markdown" if f.kind == "markdown" else "text", "label": label,
                 "text": notes.strip(), "latex": latex, "title": r.get("title"), "image_blob": None, "preview_blob": None, "width": None,
                 "height": None, "language": r.get("language"), "position": position,
                 "meta": {**base_meta, "code_blocks": len(r.get("blocks", [])), "notes": compose.is_notes_name(f.name),
                          **({"lesson": True, "lesson_title": (f.meta["lesson"] or {}).get("title")} if (f.meta or {}).get("lesson") else {})}}
            )

    items, figures = strip_nul(items), strip_nul(figures)
    async with SessionLocal() as db:
        for it in items:
            db.add(IngestItem(job_id=ctx.job_id, upload_id=upload_id, **it))
        await db.flush()
        rows = {i.key: i for i in (await db.execute(select(IngestItem).where(IngestItem.job_id == ctx.job_id))).scalars()}
        for fg in figures:
            item = rows.get(fg.pop("item_key"))
            db.add(IngestFigure(job_id=ctx.job_id, item_id=item.id if item else None, **fg))
        await db.commit()
    kinds = Counter(i["kind"] for i in items)
    summary = {"items": len(items), "figures": len(figures), "kinds": dict(kinds), "unsupported": [f"{f.name}: {f.reason}" for f in unsupported]}
    await ctx.log(f"extracted: {len(items)} items ({dict(kinds)}), {len(figures)} pictures, {len(unsupported)} skipped", stage="analyze")
    return summary


def _natural(s: str) -> list[Any]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


async def _mark_error(ctx: JobContext, f: SourceFile, reason: str) -> None:
    await ctx.log(f"{f.name}: {reason}", "error", stage="analyze", item=f.name, kind="unreadable_file")
    await _mark(f.id, "error", reason)


async def _mark(sf_id: int, status: str, reason: str) -> None:
    async with SessionLocal() as db:
        row = await db.get(SourceFile, sf_id)
        row.status, row.reason = status, reason[:1000]
        await db.commit()


async def _set_pages(sf_id: int, pages: int | None, language: str | None) -> None:
    async with SessionLocal() as db:
        row = await db.get(SourceFile, sf_id)
        row.pages, row.language = pages, language if language and language != "und" else None
        await db.commit()


def _majority(langs: list[str | None]) -> str | None:
    c = Counter(lang for lang in langs if lang and lang != "und")
    return c.most_common(1)[0][0] if c else None


async def load_items(job_id: int) -> list[IngestItem]:
    async with SessionLocal() as db:
        return list((await db.execute(select(IngestItem).where(IngestItem.job_id == job_id).order_by(IngestItem.position))).scalars())


async def load_figures(job_id: int) -> list[IngestFigure]:
    async with SessionLocal() as db:
        return list((await db.execute(select(IngestFigure).where(IngestFigure.job_id == job_id).order_by(IngestFigure.id))).scalars())


# --------------------------------------------------------------------------- 2. groups


def make_groups(items: list[IngestItem]) -> list[dict[str, Any]]:
    """A lesson is one group: its notes with its slides and the pages written by hand, one piece of text."""
    live = [it for it in items if it.kind != "skipped"]
    if not live:
        return []
    hint = next(((it.meta or {}).get("lesson_title") for it in live if (it.meta or {}).get("lesson_title")), None) or "Lezione"
    return [{"key": "g1", "item_keys": [it.key for it in live], "notes": [it.key for it in live if compose.is_notes(it)], "hint": hint,
             "first": min(it.position for it in live)}]


def group_title(g: dict[str, Any], items: list[IngestItem], parts: list[dict[str, Any]]) -> str:
    for it in items:
        if it.kind == "markdown" and it.title:
            return it.title[:200]
    if parts and parts[0].get("title"):
        return str(parts[0]["title"])[:200]
    meta_title = next(((it.meta or {}).get("meta_title") for it in items if (it.meta or {}).get("meta_title")), None)
    if meta_title and not GENERIC_TITLES.match(meta_title):
        return meta_title[:200]
    hint = re.sub(r"[_]+", " ", g.get("hint") or "").strip()
    return (hint[:1].upper() + hint[1:])[:200] if hint else "Appunti"


# --------------------------------------------------------------------------- the job


@handler("ingest")
async def ingest_job(ctx: JobContext) -> dict[str, Any]:
    upload_id = int(ctx.payload["upload_id"])
    async with SessionLocal() as db:
        up = await db.get(Upload, upload_id)
        if up is None:
            raise JobFailed("upload not found")
        target_course, target_chapter = up.target_course_id, up.target_chapter_id
        course = await db.get(Course, target_course) if target_course else None
        aiset = await settings_svc.get_section(db, "ai")

    guidelines = _guidelines(ctx, course)
    await ctx.log(
        f"linee guida della materia: {len(guidelines)} caratteri, il testo le segue" if guidelines
        else "nessuna linea guida: la struttura e lo stile del testo li decide Lecta", stage="compose", kind="guidelines")
    previous = await _previous_lesson_text(ctx, target_chapter)
    if ctx.payload.get("lesson_snapshot"):
        await ctx.progress(0.005, "preparing the lesson")
        await ctx.step("x2:lesson", lambda: lesson_pipeline.prepare(ctx, upload_id, ctx.payload["lesson_snapshot"]))
    await ctx.progress(0.01, "extracting")
    summary = await ctx.step("x2:extract", lambda: extract(ctx, upload_id))
    items = await load_items(ctx.job_id)
    if not [i for i in items if i.kind != "skipped"]:
        raise JobFailed("nothing usable in this upload: " + ("; ".join(summary.get("unsupported") or []) or "no supported files"))
    by_key = {i.key: i for i in items}

    groups = await ctx.step("x2:groups", lambda: _groups_step(items))
    units_by_group = await ctx.step("x2:units", lambda: _units_step(groups, by_key, aiset))
    figures = await load_figures(ctx.job_id)
    fig_unit: dict[str, str] = {}
    for g in groups:
        for u in units_by_group[g["key"]]:
            for k in u["items"]:
                it = by_key[k]
                for f in figures:
                    if f.item_id == it.id:
                        fig_unit[f.key] = u["key"]

    # ---- reading: every unit of every group, in parallel
    all_units = [(g, u) for g in groups for u in units_by_group[g["key"]] if u["kind"] != "latex"]
    done = 0
    langs = {g["key"]: _group_language(course, [by_key[k] for k in g["item_keys"]]) for g in groups}
    await ctx.progress(0.16, f"reading (0/{len(all_units)})")

    async def one(g: dict[str, Any], u: dict[str, Any], part: int, parts: int, prev_tail: str | None) -> None:
        nonlocal done
        u_items = read.check_unit(u, by_key)
        await ctx.step(f"x2:read:{u['key']}", lambda: read.read_unit(ctx, u, u_items, language=langs[g["key"]], part=part, parts=parts,
                                                                      prev_tail=prev_tail))
        done += 1
        await ctx.progress(0.16 + 0.6 * done / max(1, len(all_units)), f"reading ({done}/{len(all_units)})")

    coros = []
    for g in groups:
        page_units = [u for u in units_by_group[g["key"]] if u["kind"] == "pages"]
        for u in units_by_group[g["key"]]:
            if u["kind"] == "latex":
                continue
            if u["kind"] == "pages":
                idx = page_units.index(u)
                prev = page_units[idx - 1] if idx else None
                prev_tail = read.unit_text_tail([by_key[k] for k in prev["items"]]) if prev else None
                coros.append(one(g, u, idx + 1, len(page_units), prev_tail))
            else:
                coros.append(one(g, u, 1, 1, None))
    await _gather_limited(coros, aiset.max_concurrent_requests)

    # ---- writing: the study text of every group, in parallel
    budget = int(2.5 * aiset.max_output_tokens)
    written = 0
    await ctx.progress(0.78, f"composing (0/{len(groups)})")

    async def write(g: dict[str, Any]) -> None:
        nonlocal written
        g_items = [by_key[k] for k in g["item_keys"]]
        await ctx.step(f"x2:bundle:{g['key']}", lambda: _bundle_step(ctx, g, g_items, units_by_group[g["key"]], langs[g["key"]], fig_unit, budget, guidelines, previous))
        written += 1
        await ctx.progress(0.78 + 0.1 * written / max(1, len(groups)), f"composing ({written}/{len(groups)})")

    await _gather_limited([write(g) for g in groups], aiset.max_concurrent_requests)

    # ---- per group: place, check, write
    results = []
    for gi, g in enumerate(groups):
        _, bundle = await ctx.get_step(f"x2:bundle:{g['key']}")
        await ctx.progress(0.88 + 0.1 * gi / max(1, len(groups)), f"placing “{bundle['title']}”")
        outcome = await ctx.step(
            f"x2:place:{g['key']}",
            lambda bundle=bundle: placement.decide(ctx, bundle, target_course_id=target_course, target_chapter_id=target_chapter,
                                                   force_new=ctx.payload.get("placement") == "new_chapter"),
        )
        result = await place_bundle(ctx, g["key"], bundle, outcome)
        results.append({"group": bundle["title"], "notes": bundle.get("notes") or [], **result})

    async with SessionLocal() as db:
        row = await db.get(Upload, upload_id)
        row.status = "done"
        await db.commit()
    cleanup_job_dirs(ctx.job_id)
    if ctx.payload.get("lesson_id"):
        await lesson_pipeline.finish(int(ctx.payload["lesson_id"]), ctx.job_id, results)
    n_written = sum(1 for r in results if r.get("type") in ("new_chapter", "append"))
    await ctx.log(f"done: {len(groups)} group(s), {n_written} written into the course", stage="placement")
    return {"groups": results, "summary": summary}


async def place_bundle(ctx: JobContext, gkey: str, bundle: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    """Check and write one bundle where placement decided."""
    async with SessionLocal() as db:
        c = await db.get(Course, outcome["course_id"]) if outcome.get("course_id") else None
        preamble = await projects.preamble_for(db, c) if c else ((await settings_svc.get_section(db, "template")).preamble or templates.DEFAULT_PREAMBLE)
        engine = await projects.engine_for(db, c) if c else (await settings_svc.get_section(db, "latex")).engine
        lset = await settings_svc.get_section(db, "latex")
    images = bundle.get("images") or {}
    # Pictures already in the course that an updated lesson section keeps using: the scratch project needs them to compile.
    async with SessionLocal() as db:
        manifest = await projects.manifest(db, int(outcome["course_id"])) if ctx.payload.get("lesson_id") and outcome.get("course_id") else {}
    kept = {n: "blob:" + manifest[n] for n in latexmacros.image_names(bundle["body"]) if n in manifest and n.startswith("images/")}
    await ctx.progress(None, f"checking “{bundle['title']}”")
    check = await ctx.step(
        f"x2:check:{gkey}",
        lambda: apply.check_and_fix(ctx, gkey, bundle["title"], apply.with_image_paths(bundle["body"], images), images,
                                    preamble=preamble, engine=engine, language=bundle.get("language") or "it",
                                    autofix=lset.autofix_iterations, timeout=lset.timeout_s, extra_files=kept),
    )
    if check["status"] != "ok":
        await ctx.log(f"“{bundle['title']}” still has compile errors: it is written anyway, fix them in the editor", "warn",
                      stage="compile", item=bundle["title"], kind="compile_error")
    await ctx.progress(None, f"writing “{bundle['title']}”")
    target = {k: outcome[k] for k in ("type", "course_id", "chapter_id", "title") if k in outcome}
    if target["type"] == "merge":
        target["type"] = "append"
    if ctx.payload.get("lesson_id"):
        target["lesson"] = {"id": int(ctx.payload["lesson_id"]), "title": bundle["title"]}
    result = await apply.write_group(ctx, f"x2:apply:{gkey}", target, {**bundle, "body": check["body"]})
    await _mark_figures(ctx.job_id, bundle, result)
    return {**result, "compile": check["status"], "confidence": outcome.get("confidence"), "rationale": outcome.get("rationale")}


async def _mark_figures(job_id: int, bundle: dict[str, Any], result: dict[str, Any]) -> None:
    status = bundle.get("figure_status") or {}
    if not status:
        return
    path_of = result.get("image_paths") or {}
    async with SessionLocal() as db:
        figs = (await db.execute(select(IngestFigure).where(IngestFigure.job_id == job_id, IngestFigure.key.in_(list(status))))).scalars().all()
        for f in figs:
            f.status = status[f.key] if f.key in path_of or status[f.key] == "dropped" else "dropped"
            f.path = path_of.get(f.key)
        await db.commit()


def _groups_step(items: list[IngestItem]):
    async def run() -> list[dict[str, Any]]:
        groups = make_groups(items)
        async with SessionLocal() as db:
            for g in groups:
                for k in g["item_keys"]:
                    row = (await db.execute(select(IngestItem).where(IngestItem.job_id == items[0].job_id, IngestItem.key == k))).scalar_one()
                    row.group_key = g["key"]
            await db.commit()
        return groups

    return run()


def _units_step(groups: list[dict[str, Any]], by_key: dict[str, IngestItem], aiset):  # noqa: ANN001
    async def run() -> dict[str, list[dict[str, Any]]]:
        # Output ≈ input for a faithful conversion: keep units within the output budget.
        max_chars = min(aiset.chunk_chars, int(2.5 * aiset.max_output_tokens))
        out: dict[str, list[dict[str, Any]]] = {}
        n = 0
        for g in groups:
            material = [by_key[k] for k in g["item_keys"] if not compose.is_notes(by_key[k])]
            units = read.build_units(material, max_pages=aiset.chunk_pages, max_chars=max_chars)
            for u in units:
                n += 1
                u["key"] = f"u{n}"
            out[g["key"]] = units
        return out

    return run()


def _group_language(course: Course | None, items: list[IngestItem]) -> str:
    """The course's language; else the class notes' (the student's); else the material's."""
    if course is not None:
        return course.language
    return _majority([i.language for i in items if compose.is_notes(i)]) or _majority([i.language for i in items]) or "it"


def _guidelines(ctx: JobContext, course: Course | None) -> str:
    """What the student wants from the text: the guidelines given with this import, else the course's own."""
    if "guidelines" in ctx.payload:
        return str(ctx.payload.get("guidelines") or "").strip()
    return (course.guidelines or "").strip() if course is not None else ""


async def _previous_lesson_text(ctx: JobContext, chapter_id: int | None) -> str:
    """What this lesson already has in the chapter it is being written into («» for a first generation): the writing step
    updates it instead of starting over, so the chapter keeps its shape and what the student added to it."""
    lesson_id = ctx.payload.get("lesson_id")
    if not lesson_id or not chapter_id:
        return ""
    async with SessionLocal() as db:
        ch = await db.get(Chapter, chapter_id)
        text = await projects.read_text(db, ch.course_id, ch.path) if ch else None
    return (apply.lesson_section(text or "", int(lesson_id)) or "")[: compose.MAX_PREVIOUS_CHARS]


async def _bundle_step(ctx: JobContext, g: dict[str, Any], items: list[IngestItem], units: list[dict[str, Any]], language: str,
                       fig_unit: dict[str, str], budget: int, guidelines: str = "", previous: str = "") -> dict[str, Any]:
    """The material read (joined), then the study text written from the notes and the material."""
    by_key = {i.key: i for i in items}
    parts = []
    for u in units:
        if u["kind"] == "latex":
            it = by_key[u["items"][0]]
            parts.append({"unit": u["key"], "title": None, "body": it.latex or "", "drops": []})
            continue
        done, res = await ctx.get_step(f"x2:read:{u['key']}")
        if not done:
            raise JobFailed(f"unit {u['key']} was not read")
        parts.append({"unit": u["key"], **res})
        for d in res.get("drawings") or []:
            fig_unit[d] = u["key"]
    notes_items = [by_key[k] for k in g.get("notes") or [] if k in by_key]
    notes_keys = {i.id for i in notes_items}
    all_figures = await load_figures(ctx.job_id)
    figures = [f for f in all_figures if f.key in fig_unit and fig_unit[f.key] in {u["key"] for u in units}]
    notes_figures = [f for f in all_figures if f.item_id in notes_keys and f.crop_blob]
    title = group_title(g, items, [p for p in parts if p.get("title") is not None] or parts)
    material, _ = read.stitch(parts, {f.key: fig_unit[f.key] for f in figures if f.crop_blob}, title)
    if len(notes_items) > 1:
        notes = "\n\n".join(f"<!-- {(i.meta or {}).get('file_label') or i.label} -->\n{i.text or ''}" for i in notes_items)
    else:
        notes = notes_items[0].text or "" if notes_items else ""
    labels = list(dict.fromkeys((i.meta or {}).get("file_label") or i.label for i in items))
    notes_labels = [(i.meta or {}).get("file_label") or i.label for i in notes_items]
    if not notes_items:
        await ctx.log(f"“{title}”: no class notes (.md/.txt) in this group, the text is a summary of the material", stage="compose",
                      item=title, kind="no_notes")
    if notes.strip() or material.strip():
        text = await compose.compose(ctx, g["key"], notes=notes, material=material, title=title, language=language, budget_chars=budget,
                                     label=", ".join(labels)[:300], guidelines=guidelines, previous=previous)
    else:
        text = {"title": None, "bodies": []}
    title = group_title(g, items, [{"title": text["title"]}, *parts])
    known = {f.key: "text" for f in [*figures, *notes_figures] if f.crop_blob}
    body, status = read.stitch([{"unit": f"c{n}", "body": b, "drops": []} for n, b in enumerate(text["bodies"])], known, title,
                               append_unplaced=False)
    if not body.strip():
        body = "\\review{Nessun contenuto riconosciuto nel materiale caricato.}"
    by_fig = {f.key: f for f in [*figures, *notes_figures]}
    images = {k: {"blob": by_fig[k].crop_blob, "ext": image_ext(by_fig[k].crop_blob)} for k, st in status.items() if st != "dropped"}
    return {
        "group_key": g["key"], "title": title, "language": language, "body": body, "images": images, "figure_status": status,
        "source_file_ids": sorted({i.source_file_id for i in items}), "label": ", ".join(labels)[:300], "notes": notes_labels,
        "outline": [m.group(1) for m in re.finditer(r"\\section\*?\{([^}]*)\}", body)][:40],
    }


async def _gather_limited(coros: list, limit: int) -> None:
    sem = asyncio.Semaphore(limit)

    async def run(c):
        async with sem:
            return await c

    results = await asyncio.gather(*(run(c) for c in coros), return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r


def course_slug(title: str) -> str:
    return slugify(title, 40)
