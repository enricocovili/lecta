"""Candidate chapters for placing new material.

Lexical retrieval uses PostgreSQL full-text search with each course's
text-search configuration (italian / english / simple …). The semantic part
(local embeddings + pgvector) is added by services/semantic.py when enabled;
`candidates()` combines both, or falls back to lexical-only.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Chapter, Course
from . import templates
from .texttools import _STOP, headings, latex_to_text

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]{4,}")
_ALL_STOP = {w for ws in _STOP.values() for w in ws}


def keywords(text_: str, n: int = 30) -> list[str]:
    words = [w.lower() for w in _WORD_RE.findall(text_)]
    counts = Counter(w for w in words if w not in _ALL_STOP)
    return [w for w, _ in counts.most_common(n)]


async def outlines(db: AsyncSession, chapter_ids: list[int]) -> dict[int, list[str]]:
    """Section outline of each chapter, extracted locally from its source."""
    if not chapter_ids:
        return {}
    rows = await db.execute(
        text(
            "SELECT c.id, pf.text_content FROM chapters c JOIN project_files pf "
            "ON pf.course_id = c.course_id AND pf.path = c.path WHERE c.id = ANY(:ids)"
        ),
        {"ids": chapter_ids},
    )
    out = {}
    for cid, src in rows.all():
        out[cid] = [f"{'  ' if lvl == 'subsection' else ''}{t}" for lvl, t in headings(src or "") if lvl in ("section", "subsection")][:40]
    return out


async def lexical(db: AsyncSession, query_text: str, *, course_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
    kws = keywords(query_text)
    if not kws:
        return []
    courses = (await db.execute(select(Course) if course_id is None else select(Course).where(Course.id == course_id))).scalars().all()
    by_lang: dict[str, list[int]] = {}
    for c in courses:
        by_lang.setdefault(templates.ts_config(c.language), []).append(c.id)
    results: list[dict[str, Any]] = []
    kw_text = " ".join(kws)
    for cfg, ids in by_lang.items():
        rows = await db.execute(
            text(
                """
                WITH q AS (SELECT array(SELECT DISTINCT unnest(tsvector_to_array(to_tsvector(CAST(:cfg AS regconfig), :kw)))) AS lex)
                SELECT ch.id, ch.title, ch.course_id,
                       (SELECT count(*) FROM unnest(q.lex) l WHERE l = ANY(tsvector_to_array(pf.tsv)))::float
                         / GREATEST(array_length(q.lex, 1), 1) AS score
                FROM chapters ch
                JOIN project_files pf ON pf.course_id = ch.course_id AND pf.path = ch.path, q
                WHERE ch.course_id = ANY(:ids) AND pf.tsv IS NOT NULL
                ORDER BY score DESC
                LIMIT :limit
                """
            ),
            {"cfg": cfg, "kw": kw_text, "ids": ids, "limit": limit},
        )
        for cid, title, course, score in rows.all():
            results.append({"chapter_id": cid, "chapter_title": title, "course_id": course, "lexical": float(score or 0)})
    # Chapter titles count too (a new chapter's title often names the topic).
    for r in results:
        title_words = set(keywords(r["chapter_title"], 10))
        if title_words & set(kws[:10]):
            r["lexical"] = min(1.0, r["lexical"] + 0.15)
    results.sort(key=lambda r: r["lexical"], reverse=True)
    return results[:limit]


async def candidates(
    db: AsyncSession, query_text: str, *, title: str = "", course_id: int | None = None, top_k: int = 8
) -> tuple[list[dict[str, Any]], str]:
    """Top-k candidate chapters (+ courses without chapters). Returns (candidates, mode)."""
    from . import semantic

    q = f"{title}\n{latex_to_text(query_text)}"
    lex = await lexical(db, q, course_id=course_id, limit=max(top_k * 3, 20))
    mode = "lexical"
    scores: dict[int, dict[str, Any]] = {r["chapter_id"]: dict(r, semantic=None) for r in lex}
    sem = await semantic.search(db, q, course_id=course_id, limit=max(top_k * 3, 20))
    if sem is not None:
        mode = "hybrid"
        for r in sem:
            d = scores.setdefault(r["chapter_id"], {**r, "lexical": 0.0})
            d["semantic"] = r["semantic"]
    for d in scores.values():
        if mode == "hybrid":
            d["score"] = round(0.4 * d.get("lexical", 0.0) + 0.6 * max(0.0, d.get("semantic") or 0.0), 4)
        else:
            d["score"] = round(d.get("lexical", 0.0), 4)
    ranked = sorted(scores.values(), key=lambda d: d["score"], reverse=True)[:top_k]
    # Fill in names and outlines.
    course_rows = {c.id: c for c in (await db.execute(select(Course))).scalars()}
    outs = await outlines(db, [d["chapter_id"] for d in ranked])
    for d in ranked:
        c = course_rows.get(d["course_id"])
        d["course_name"] = c.name if c else ""
        d["outline"] = outs.get(d["chapter_id"], [])
    # Courses without any chapter are candidates for a new chapter.
    with_chapters = {cid for (cid,) in (await db.execute(select(Chapter.course_id).distinct())).all()}
    for c in course_rows.values():
        if (course_id is None or c.id == course_id) and c.id not in with_chapters:
            overlap = len(set(keywords(c.name + " " + " ".join(c.tags or []), 10)) & set(keywords(q, 30)))
            ranked.append(
                {"course_id": c.id, "course_name": c.name, "chapter_id": None, "chapter_title": None, "outline": [],
                 "score": round(min(0.9, 0.3 * overlap), 4), "lexical": 0.0, "semantic": None}
            )
    return ranked, mode
