"""Reading: extracted pages/photos → LaTeX, in parallel units (the material the text is written from).

A unit is a run of consecutive pages of one file (bounded by pages, characters and
page pictures) or one photo. Every page sends its text layer (with [[IMG id]]
markers where pictures sit); pages that need it also send their picture. The
model replies in plain text (JSON would mangle LaTeX backslashes):

    %%TITLE: …
    %%FIGURE <id> <page> <x0> <y0> <x1> <y1>
    %%DROP <id>
    <LaTeX body>
    %%END

A reply cut off at the output limit splits the unit in two and tries again.
"""

from __future__ import annotations

import re
from typing import Any

from ..db import SessionLocal
from ..models import IngestFigure, IngestItem
from ..worker.context import JobContext, JobFailed, run_cpu
from . import analyze
from ..services import latexmacros
from .apply import sanitize_body
from .common import ReplyTruncated, ai
from .requests import RequestBuilder

LANG_NAMES = {"it": "Italian", "en": "English", "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese"}
MAX_IMAGES_PER_UNIT = 4
MAX_SPLITS = 3
_FIGURE_RE = re.compile(r"^%%FIGURE\s+(\S+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$")
_DROP_RE = re.compile(r"^%%DROP\s+(\S+)\s*$")
_TITLE_RE = re.compile(r"^%%TITLE:\s*(.*)$")
_SECTION_RE = re.compile(r"^\\section\*?\{([^}]*)\}\s*$", re.M)
_TIKZ_RE = re.compile(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", re.S)
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics\s*(\[[^\]]*\])?\s*\{[^}]*\}")


# --------------------------------------------------------------------------- units


def build_units(items: list[IngestItem], *, max_pages: int, max_chars: int) -> list[dict[str, Any]]:
    """Split a group's items (in order) into units. Markdown/text items need no model."""
    units: list[dict[str, Any]] = []
    cur: list[IngestItem] = []
    chars = 0
    images = 0

    def flush() -> None:
        nonlocal cur, chars, images
        if cur:
            units.append({"kind": "pages", "items": [i.key for i in cur]})
        cur, chars, images = [], 0, 0

    for it in items:
        if it.kind in ("markdown", "text"):
            flush()
            units.append({"kind": "latex", "items": [it.key]})
            continue
        if it.kind in ("photo", "handwritten"):
            flush()
            units.append({"kind": "photo", "items": [it.key]})
            continue
        size = len(it.text or "")
        eyes = 1 if it.image_blob else 0
        same_file = not cur or cur[-1].source_file_id == it.source_file_id
        if cur and (not same_file or len(cur) >= max_pages or chars + size > max_chars or images + eyes > MAX_IMAGES_PER_UNIT):
            flush()
        cur.append(it)
        chars += size
        images += eyes
    flush()
    for n, u in enumerate(units, start=1):
        u["key"] = f"u{n}"
    return units


# --------------------------------------------------------------------------- replies


def parse_reply(text: str) -> dict[str, Any]:
    """Split a reply into title, drawings to crop, dropped picture ids and the LaTeX body."""
    t = text.strip()
    fence = re.match(r"^```(?:latex|tex|text)?\s*(.*?)```$", t, re.S)
    if fence:
        t = fence.group(1).strip()
    title = None
    figures: list[dict[str, Any]] = []
    drops: list[str] = []
    body: list[str] = []
    complete = False
    for ln in t.split("\n"):
        s = ln.strip()
        if s == "%%END":
            complete = True
            break
        m = _TITLE_RE.match(s)
        if m:
            title = m.group(1).strip()[:200] or title
            continue
        m = _FIGURE_RE.match(s)
        if m:
            try:
                bbox = [max(0.0, min(1.0, float(m.group(k)))) for k in (3, 4, 5, 6)]
            except ValueError:
                continue
            if bbox[2] - bbox[0] >= 0.02 and bbox[3] - bbox[1] >= 0.02:
                figures.append({"id": m.group(1), "page": int(m.group(2)), "bbox": bbox})
            continue
        m = _DROP_RE.match(s)
        if m:
            drops.append(m.group(1))
            continue
        if s.startswith("%%"):
            continue
        body.append(ln)
    return {"title": title, "figures": figures, "drops": drops, "body": "\n".join(body).strip(), "complete": complete}


# --------------------------------------------------------------------------- one unit


async def read_unit(ctx: JobContext, unit: dict[str, Any], items: list[IngestItem], *, language: str, part: int, parts: int,
                    prev_tail: str | None, key: str | None = None, depth: int = 0) -> dict[str, Any]:
    """Convert one unit; returns {title, body, pictures: [ids placed or appended], drops, drawings: [new figure rows]}."""
    key = key or unit["key"]
    label = (items[0].meta or {}).get("file_label") or items[0].label
    lang = LANG_NAMES.get(language, language)
    if unit["kind"] == "photo":
        it = items[0]
        prompt, role = "read.handwriting", "handwriting"
        rb = RequestBuilder(prompt)
        instr = f"The material is probably in {lang}. Photo: {it.label}. Picture ids for drawings: p1n1, p1n2, …"
        if (it.meta or {}).get("lesson"):
            instr = (f"The material is probably in {lang}. This is a page the student wrote by hand with a pen during the lecture ({it.label}), "
                     "on a blank page: transcribe it. Picture ids for drawings: p1n1, p1n2, …")
        rb.instr(instr)
        rb.image(it.image_blob, it.width, it.height, it.label)
        meta = {"label": it.label, "language": language, "unit": key, "looks_like_photo": it.kind == "photo"}
    else:
        prompt = "read.pages"
        eyes = [i for i in items if i.image_blob]
        role = "vision" if eyes else "writing"
        p0, p1 = items[0].page, items[-1].page
        rb = RequestBuilder(prompt)
        instr = f"The material is probably in {lang}. Material: {label}, pages {p0}-{p1} (part {part} of {parts})."
        if part > 1:
            instr += " Earlier parts were converted separately: continue, don't restart the document; give %%TITLE only in part 1."
        if (items[0].meta or {}).get("lesson"):
            instr += (" These are slides of a lecture. Begin the content of every slide with a LaTeX comment line `% slide N` (N = its page number), so"
                      " that it can be matched with the student's notes of that slide.")
        rb.instr(instr)
        if prev_tail:
            rb.data(prev_tail, "end of the previous page (context only, already converted: do not repeat it)")
        for it in items:
            rb.data(it.text or "(no text layer on this page)", f"{label} · page {it.page} · text layer")
            if it.image_blob:
                what = "picture of the page, with the student's handwriting on it (pen strokes over the slide)" if (it.meta or {}).get("annotated") else "picture of the page"
                rb.image(it.image_blob, it.width, it.height, f"{label} · page {it.page} · {what}")
        meta = {"unit": key, "label": label, "language": language,
                "pages": [{"page": i.page, "title": i.title, "text": i.text or "", "has_image": bool(i.image_blob),
                           "images": (i.meta or {}).get("pictures", [])} for i in items]}
    async with SessionLocal() as db:
        req = await rb.build(db, role=role, task=prompt, request_key=f"{ctx.job_id}:read:{key}", json_output=False, temperature=0.1, meta=meta)
    try:
        res = await ai(ctx, req, title=f"Lettura di {label}" + (f" (p. {items[0].page}-{items[-1].page})" if items[0].page else ""))
    except ReplyTruncated:
        if len(items) < 2 or depth >= MAX_SPLITS:
            raise
        await ctx.log(f"{label}: reply cut off at the output limit, reading the pages in two halves", "warn", stage="read", item=label,
                      kind="split_unit")
        half = len(items) // 2
        a = await read_unit(ctx, unit, items[:half], language=language, part=part, parts=parts, prev_tail=prev_tail, key=f"{key}a", depth=depth + 1)
        b = await read_unit(ctx, unit, items[half:], language=language, part=part, parts=parts,
                            prev_tail=(items[half - 1].text or "")[-300:], key=f"{key}b", depth=depth + 1)
        return {"title": a["title"] or b["title"], "body": a["body"].rstrip() + "\n\n" + b["body"].lstrip(),
                "drops": a["drops"] + b["drops"], "drawings": a["drawings"] + b["drawings"]}
    reply = parse_reply(res.text)
    if not reply["complete"]:
        await ctx.log(f"{label}: the reply has no %%END line; using it as it is", "warn", stage="read", item=label, kind="reply_unterminated")
    drawings = await _crop_drawings(ctx, unit, items, reply["figures"], key)
    body = reply["body"]
    for d in drawings:
        body = body.replace("{" + d["model_id"] + "}", "{" + d["key"] + "}")
    return {"title": reply["title"], "body": body, "drops": reply["drops"], "drawings": [d["key"] for d in drawings]}


async def _crop_drawings(ctx: JobContext, unit: dict[str, Any], items: list[IngestItem], figures: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Drawings the model saw in a page picture (not given as [[IMG]]): crop them from that picture."""
    out = []
    by_page = {i.page or 1: i for i in items}
    for n, f in enumerate(figures[:8], start=1):
        it = by_page.get(f["page"]) if unit["kind"] != "photo" else items[0]
        if it is None or not it.image_blob:
            continue
        blob, w, h = await run_cpu(analyze.crop_image, it.image_blob, f["bbox"])
        fkey = f"{ctx.job_id}{key}d{n}"
        async with SessionLocal() as db:
            db.add(IngestFigure(job_id=ctx.job_id, item_id=it.id, key=fkey, origin="drawing", source_ref=f"source:{it.source_file_id}",
                                crop_blob=blob, bbox=f["bbox"], group_key=it.group_key))
            await db.commit()
        out.append({"model_id": f["id"], "key": fkey})
    return out


# --------------------------------------------------------------------------- stitching


def stitch(parts: list[dict[str, Any]], known: dict[str, str], chapter_title: str, *, append_unplaced: bool = True) -> tuple[str, dict[str, str]]:
    """Join unit bodies; resolve picture ids. `known` maps picture key → unit key it came from.
    Pictures a unit didn't place are appended at its end (unless append_unplaced is off).
    Returns (body, status per picture key: used | appended | dropped)."""
    status: dict[str, str] = {}
    out: list[str] = []
    last_section: str | None = None
    dropped = {d for p in parts for d in p.get("drops", [])}
    seen: set[str] = set()
    for p in parts:
        body = _TIKZ_RE.sub("", p["body"])
        body = _INCLUDEGRAPHICS_RE.sub("", body)

        def place(k: str) -> str | None:
            if not k:
                return None
            if k.startswith("images/"):
                return None  # a picture already in the course, kept by an updated lesson section
            if k not in known or k in seen:
                return ""  # unknown or already placed: the picture goes, the text beside it stays
            seen.add(k)
            status[k] = "used"
            return None

        body = latexmacros.map_images(body, place)
        # A unit that continues the previous one's section doesn't repeat its heading.
        m = _SECTION_RE.search(body)
        if m and last_section and m.group(1).strip().lower() == last_section and not body[: m.start()].strip():
            body = body[m.end():].lstrip("\n")
        if not out:
            m = _SECTION_RE.search(body)
            if m and m.group(1).strip().lower() == chapter_title.strip().lower() and not body[: m.start()].strip():
                body = body[m.end():].lstrip("\n")
        # Pictures of this unit the model didn't place (and didn't drop) go at its end.
        for k, unit in known.items():
            if append_unplaced and unit == p["unit"] and k not in seen and k not in dropped:
                body = body.rstrip() + f"\n\n\\lectaimage{{{k}}}\n"
                seen.add(k)
                status[k] = "appended"
        sections = _SECTION_RE.findall(body)
        if sections:
            last_section = sections[-1].strip().lower()
        out.append(body.strip())
    for k in known:
        status.setdefault(k, "dropped")
    return sanitize_body("\n\n".join(x for x in out if x)), status


def unit_text_tail(items: list[IngestItem]) -> str | None:
    if not items or not items[-1].text:
        return None
    return items[-1].text[-300:]


def check_unit(unit: dict[str, Any], by_key: dict[str, IngestItem]) -> list[IngestItem]:
    items = [by_key[k] for k in unit["items"] if k in by_key]
    if not items:
        raise JobFailed(f"unit {unit['key']} has no items")
    return items
