"""Admin full-text search over LaTeX sources, and "Export all"."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..db import get_db
from ..models import Chapter, Course, ProjectFile, Publication, SourceFile
from ..security.auth import require_admin
from ..services import blobs, compile as compile_svc, projects, templates
from ..services.texttools import keywords_simple

router = APIRouter(dependencies=[Depends(require_admin)])


def _first_line(src: str, terms: list[str]) -> int | None:
    low = src.lower()
    best = None
    for t in terms:
        i = low.find(t.lower())
        if i >= 0 and (best is None or i < best):
            best = i
    return src.count("\n", 0, best) + 1 if best is not None else None


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=200), db: AsyncSession = Depends(get_db)) -> dict:
    terms = keywords_simple(q)
    results = []
    courses = {c.id: c for c in (await db.execute(select(Course))).scalars()}
    chapters = {(ch.course_id, ch.path): ch for ch in (await db.execute(select(Chapter))).scalars()}
    by_cfg: dict[str, list[int]] = {}
    for c in courses.values():
        by_cfg.setdefault(templates.ts_config(c.language), []).append(c.id)
    for cfg, ids in by_cfg.items():
        rows = await db.execute(
            text(
                """
                SELECT pf.course_id, pf.path, ts_rank_cd(pf.tsv, query) AS rank,
                       ts_headline(CAST(:cfg AS regconfig), pf.text_content, query,
                                   'StartSel=<<, StopSel=>>, MaxWords=30, MinWords=12, MaxFragments=2') AS snippet
                FROM project_files pf, websearch_to_tsquery(CAST(:cfg AS regconfig), :q) query
                WHERE pf.course_id = ANY(:ids) AND pf.tsv @@ query
                ORDER BY rank DESC LIMIT 50
                """
            ),
            {"cfg": cfg, "q": q, "ids": ids},
        )
        for course_id, path, rank, snippet in rows.all():
            results.append({"course_id": course_id, "path": path, "rank": float(rank), "snippet": snippet})
    # Plain substring fallback (e.g. LaTeX commands, which the text-search config drops).
    if not results or re.search(r"[\\{}$]", q):
        rows = await db.execute(
            select(ProjectFile.course_id, ProjectFile.path, ProjectFile.text_content)
            .where(ProjectFile.is_text.is_(True), ProjectFile.text_content.ilike(f"%{q.replace('%', '').replace('_', '')}%"))
            .limit(50)
        )
        seen = {(r["course_id"], r["path"]) for r in results}
        for course_id, path, content in rows.all():
            if (course_id, path) in seen:
                continue
            i = (content or "").lower().find(q.lower())
            snippet = (content or "")[max(0, i - 80): i + len(q) + 80].replace(q, f"<<{q}>>") if i >= 0 else ""
            results.append({"course_id": course_id, "path": path, "rank": 0.0, "snippet": snippet})
    out = []
    for r in results:
        c = courses.get(r["course_id"])
        ch = chapters.get((r["course_id"], r["path"]))
        pf = await projects.get_file(db, r["course_id"], r["path"])
        line = _first_line(pf.text_content or "", terms or [q]) if pf else None
        out.append(
            {**r, "course_name": c.name if c else "", "chapter_id": ch.id if ch else None, "chapter_title": ch.title if ch else None,
             "line": line}
        )
    out.sort(key=lambda r: r["rank"], reverse=True)
    title_hits = [
        {"type": "course", "id": c.id, "title": c.name}
        for c in courses.values()
        if q.lower() in c.name.lower()
    ] + [
        {"type": "chapter", "id": ch.id, "course_id": ch.course_id, "title": ch.title, "course_name": courses[ch.course_id].name}
        for ch in chapters.values()
        if q.lower() in ch.title.lower()
    ]
    sources = (
        await db.execute(select(SourceFile.id, SourceFile.name, SourceFile.kind).where(SourceFile.name.ilike(f"%{q}%")).limit(20))
    ).all()
    return {
        "files": out[:100],
        "titles": title_hits[:50],
        "sources": [{"id": i, "name": n, "kind": k} for i, n, k in sources],
    }


# --------------------------------------------------------------------------- export


class _Sink(io.RawIOBase):
    """A write-only, non-seekable stream that hands out what was written so far."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:  # noqa: ANN001
        self.buf += b
        return len(b)

    def take(self) -> bytes:
        data = bytes(self.buf)
        self.buf.clear()
        return data


def _zip_stream(entries: list[tuple[str, str | bytes | None, str | None]]) -> Iterator[bytes]:
    """entries: (arcname, inline data or None, blob hash or None). Streams without temp files."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, data, blob in entries:
            info = zipfile.ZipInfo(name, date_time=datetime.now(UTC).timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            with z.open(info, "w", force_zip64=True) as f:
                if data is not None:
                    f.write(data.encode() if isinstance(data, str) else data)
                elif blob and blobs.exists(blob):
                    with open(blobs.path_for(blob), "rb") as src:
                        while chunk := src.read(1024 * 1024):
                            f.write(chunk)
                            yield sink.take()
            yield sink.take()
    yield sink.take()


@router.get("/export")
async def export_all(include_sources: bool = False, db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    """Zip of every course's LaTeX sources, its latest draft PDF and published PDFs
    (optionally the uploaded originals too)."""
    entries: list[tuple[str, str | bytes | None, str | None]] = []
    courses = (await db.execute(select(Course).order_by(Course.slug))).scalars().all()
    readme = ["Lecta export " + datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), ""]
    for c in courses:
        base = f"{c.slug}/"
        for pf in await projects.get_files(db, c.id):
            entries.append((base + "src/" + pf.path, None, pf.blob))
        entries.append((base + "src/preamble.tex", templates.with_compat(await projects.preamble_for(db, c)), None))
        draft = projects.safe_read_output(compile_svc.draft_pdf_path(c.id))
        if draft:
            entries.append((base + "draft.pdf", draft, None))
        if c.current_publication_id:
            pub = await db.get(Publication, c.current_publication_id)
            if pub:
                entries.append((base + "published.pdf", None, pub.pdf_blob))
        readme.append(f"{c.slug}: {c.name} ({c.academic_year or ''}, {c.language})")
    if include_sources:
        for sf in (await db.execute(select(SourceFile).where(SourceFile.blob.is_not(None)))).scalars():
            safe = re.sub(r"[^\w.\- ]", "_", sf.name)[:120]
            entries.append((f"_sources/{sf.upload_id}/{sf.id}-{safe}", None, sf.blob))
    entries.insert(0, ("README.txt", "\n".join(readme) + "\n", None))
    name = f"lecta-export-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}.zip"
    return StreamingResponse(
        _zip_stream(entries), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


_ = config
