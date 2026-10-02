"""Writing: the chapter's study text, in prose, from the class notes and the material.

The class notes (.md/.txt, the bullet points taken during the lecture) are the
backbone: the text follows their order and structure, and the material read
from slides, handouts and photos fills in definitions, formulas and examples.
Without notes the material alone is summarised into the same kind of text.

A group whose text wouldn't fit one reply is written in parts, in parallel:
with notes, each part covers a slice of the notes and sees all the material
(or its most relevant sections, when there is a lot of it); without notes,
each part covers a slice of the material. A reply cut off at the output limit
splits its part in two and tries again.
"""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any

from ..db import SessionLocal
from ..models import IngestItem
from ..worker.context import JobContext
from .common import ReplyTruncated, ai
from .read import LANG_NAMES, MAX_SPLITS, parse_reply
from .requests import RequestBuilder

NOTES_EXT = (".md", ".markdown", ".mdown", ".txt")
# Expected length of the text relative to its sources: notes get expanded into prose, the material summarised.
NOTES_GROWTH = 2.5
MATERIAL_WITH_NOTES = 0.35
MATERIAL_ALONE = 0.45
PART_FILL = 0.7  # of the output budget, to leave room for a longer than expected reply
MAX_MATERIAL_CHARS = 150_000  # material sent with each part (the most relevant sections beyond this)
MAX_PARTS = 12
MAX_PREVIOUS_CHARS = 120_000  # of the earlier version of a lesson's text sent along when it is updated
GUIDELINES_INTRO = (
    "The student's guidelines for writing this subject. Follow them for structure, tone, level of detail, what to stress and what to leave out; "
    "they never override the reply format or the rules about formulas, pictures and not inventing content:"
)
_WORD = re.compile(r"[^\W\d_]{4,}", re.U)
_LATEX_CMD = re.compile(r"\\[a-zA-Z]+")


def is_notes_name(name: str | None) -> bool:
    """Class notes are Markdown or plain-text files (a .tex file is material, not notes)."""
    n = (name or "").lower()
    return n.endswith(NOTES_EXT) or ("." not in n.rsplit("/", 1)[-1])


def is_notes(item: IngestItem) -> bool:
    meta = item.meta or {}
    return item.kind in ("markdown", "text") and bool(meta.get("notes", is_notes_name(meta.get("file"))))


# --------------------------------------------------------------------------- splitting


def _cut(text: str, n: int, boundaries: list[list[int]]) -> list[str]:
    """Cut text in n pieces of similar length, at the best boundaries available.
    `boundaries` are offsets grouped by preference (headings first, then paragraphs, …)."""
    if n <= 1 or len(text) < 2:
        return [text]
    size = len(text) / n
    cuts: list[int] = []
    for k in range(1, n):
        target = k * size
        pick = None
        for group in boundaries:
            near = [b for b in group if abs(b - target) <= 0.35 * size and (not cuts or b > cuts[-1])]
            if near:
                pick = min(near, key=lambda b: abs(b - target))
                break
        if pick is None:
            pick = next((b for group in boundaries for b in sorted(group, key=lambda b: abs(b - target)) if not cuts or b > cuts[-1]), None)
        if pick is not None and 0 < pick < len(text):
            cuts.append(pick)
    pieces = [text[a:b] for a, b in zip([0, *cuts], [*cuts, len(text)])]
    return [p for p in pieces if p.strip()]


def _line_starts(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for ln in text.split("\n"):
        out.append((pos, ln))
        pos += len(ln) + 1
    return out


def split_notes(text: str, n: int) -> list[str]:
    """Notes → n slices, cut at headings, else at top-level bullets/paragraphs, else at any line."""
    lines = _line_starts(text)
    heads = [o for o, ln in lines if re.match(r"^#{1,6}\s", ln)]
    top = [o for i, (o, ln) in enumerate(lines) if ln.strip() and not ln[:1].isspace() and (i == 0 or not lines[i - 1][1].strip() or re.match(r"^([-*+]|\d+[.)])\s", ln))]
    anyline = [o for o, _ln in lines]
    return _cut(text, n, [heads, top, anyline])


def split_latex(text: str, n: int) -> list[str]:
    """LaTeX → n slices, cut before \\section, else \\subsection, else a blank line outside environments."""
    sections, subsections, paragraphs, anyline = [], [], [], []
    depth = 0
    lines = _line_starts(text)
    for i, (o, ln) in enumerate(lines):
        s = ln.strip()
        if depth == 0:
            if s.startswith("\\section"):
                sections.append(o)
            elif s.startswith("\\subsection"):
                subsections.append(o)
            elif not s and i + 1 < len(lines):
                paragraphs.append(lines[i + 1][0])
            anyline.append(o)
        depth = max(0, depth + len(re.findall(r"\\begin\{", ln)) - len(re.findall(r"\\end\{", ln)))
    return _cut(text, n, [sections, subsections, paragraphs, anyline])


def outline_of_notes(text: str) -> str:
    return "\n".join(ln for ln in text.split("\n") if re.match(r"^#{1,6}\s", ln))[:4000]


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(_LATEX_CMD.sub(" ", text))}


def relevant_material(material: str, notes: str, limit: int) -> str:
    """The sections of the material most related to this slice of the notes, in their order, within limit."""
    if len(material) <= limit:
        return material
    chunks = split_latex(material, max(2, math.ceil(len(material) / 6000)))
    want = _words(notes)
    scored = sorted(range(len(chunks)), key=lambda i: -len(want & _words(chunks[i])) / math.sqrt(1 + len(_words(chunks[i]))))
    keep, total = set(), 0
    for i in scored:
        if total + len(chunks[i]) > limit:
            continue
        keep.add(i)
        total += len(chunks[i])
    return "\n\n".join(chunks[i].strip() for i in sorted(keep))


def plan_parts(notes: str, material: str, budget_chars: int) -> list[dict[str, str]]:
    """The parts one group's text is written in: [{notes, material}]."""
    est = NOTES_GROWTH * len(notes) + MATERIAL_WITH_NOTES * len(material) if notes.strip() else MATERIAL_ALONE * len(material)
    n = min(MAX_PARTS, max(1, math.ceil(est / (PART_FILL * budget_chars))))
    if notes.strip():
        return [{"notes": piece, "material": relevant_material(material, piece, MAX_MATERIAL_CHARS)} for piece in split_notes(notes, n)]
    return [{"notes": "", "material": piece} for piece in split_latex(material, n)]


# --------------------------------------------------------------------------- writing


async def compose(ctx: JobContext, gkey: str, *, notes: str, material: str, title: str, language: str, budget_chars: int,
                  label: str, guidelines: str = "", previous: str = "") -> dict[str, Any]:
    """Write one group's text. `previous` is the text this group already has in the course (a lesson written again):
    the new text updates it. Returns {title, bodies (one per part, in order), parts, with_notes}."""
    parts = plan_parts(notes, material, budget_chars)
    outline = outline_of_notes(notes) if len(parts) > 1 else ""
    if len(parts) > 1:
        await ctx.log(f"“{title}”: the text is written in {len(parts)} parts", stage="compose", item=title, kind="compose_parts")
    results = await asyncio.gather(*(
        _part(ctx, f"{gkey}p{k}", p, part=k, parts=len(parts), outline=outline, title=title, language=language, label=label,
              prev=_last_heading(parts[k - 2]) if k > 1 else None, guidelines=guidelines, previous=previous)
        for k, p in enumerate(parts, start=1)
    ))
    return {"title": next((r["title"] for r in results if r["title"]), None), "bodies": [r["body"] for r in results],
            "parts": len(parts), "with_notes": bool(notes.strip())}


def _last_heading(part: dict[str, str]) -> str | None:
    src = part["notes"] or part["material"]
    heads = re.findall(r"^#{1,6}\s+(.+)$", src, re.M) if part["notes"] else re.findall(r"\\(?:sub)?section\*?\{([^}]*)\}", src)
    return heads[-1].strip()[:200] if heads else None


async def _part(ctx: JobContext, key: str, p: dict[str, str], *, part: int, parts: int, outline: str, title: str, language: str,
                label: str, prev: str | None, depth: int = 0, guidelines: str = "", previous: str = "") -> dict[str, Any]:
    lang = LANG_NAMES.get(language, language)
    with_notes = bool(p["notes"].strip())
    rb = RequestBuilder("notes.compose")
    instr = [f"Write in {lang}. Chapter: “{title}”. Sources: {label}."]
    if with_notes:
        instr.append("The student's class notes are given: they are the backbone of the text.")
    else:
        instr.append("No class notes were given: the material alone is the source; summarise it into study text.")
    if parts > 1:
        what = "this slice of the class notes" if with_notes else "this slice of the material"
        instr.append(f"This is part {part} of {parts}: write the text for {what} only; the other parts are written separately and joined"
                     " after yours. Give %%TITLE only in part 1.")
        if prev:
            instr.append(f"The previous part ends with “{prev}”: continue from there, don't repeat it and don't restart the chapter.")
    rb.instr(" ".join(instr))
    if guidelines:
        # The student's own wishes for this subject (not uploaded material, so not fenced as data).
        rb.instr(GUIDELINES_INTRO + "\n" + guidelines)
    if with_notes:
        rb.data(p["notes"], "class notes (the backbone)" + (f", part {part} of {parts}" if parts > 1 else ""))
        if outline and parts > 1:
            rb.data(outline, "headings of the whole class notes (context only)")
    if p["material"].strip():
        rb.data(p["material"], "material: slides, handouts and photos converted to LaTeX")
    if previous.strip():
        rb.instr("The text of this lesson is already in the chapter (given below as the PREVIOUS VERSION): update it instead of writing "
                 "it from scratch, as the PREVIOUS VERSION rules of your instructions say."
                 + (" It covers the whole lesson; update only the portion that corresponds to your slice." if parts > 1 else ""))
        rb.data(previous, "PREVIOUS VERSION of this lesson's text, already in the chapter")
    async with SessionLocal() as db:
        req = await rb.build(db, role="writing", task="notes.compose", request_key=f"{ctx.job_id}:compose:{key}", json_output=False,
                             temperature=0.3, meta={"notes": p["notes"], "material": p["material"], "part": part, "parts": parts,
                                                    "title": title, "language": language, "guidelines": guidelines, "previous": previous})
    try:
        res = await ai(ctx, req, title=f"Stesura di “{title}”" + (f" ({part}/{parts})" if parts > 1 else ""))
    except ReplyTruncated:
        src = p["notes"] if with_notes else p["material"]
        halves = (split_notes if with_notes else split_latex)(src, 2)
        if len(halves) < 2 or depth >= MAX_SPLITS:
            raise
        await ctx.log(f"“{title}”: reply cut off at the output limit, writing that part in two halves", "warn", stage="compose", item=title,
                      kind="split_part")
        subs = [{"notes": h, "material": p["material"]} if with_notes else {"notes": "", "material": h} for h in halves]
        out = await asyncio.gather(*(
            _part(ctx, f"{key}{'ab'[n]}", sub, part=2 * part - 1 + n, parts=2 * parts, outline=outline or outline_of_notes(p["notes"]),
                  title=title, language=language, label=label, prev=prev if n == 0 else _last_heading(subs[0]), depth=depth + 1,
                  guidelines=guidelines, previous=previous)
            for n, sub in enumerate(subs)
        ))
        return {"title": out[0]["title"] or out[1]["title"], "body": out[0]["body"].rstrip() + "\n\n" + out[1]["body"].lstrip()}
    reply = parse_reply(res.text)
    if not reply["complete"]:
        await ctx.log(f"“{title}”: the reply has no %%END line; using it as it is", "warn", stage="compose", item=title, kind="reply_unterminated")
    return {"title": reply["title"], "body": reply["body"]}
