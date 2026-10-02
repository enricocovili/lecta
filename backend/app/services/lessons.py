"""Lessons: the ink of a page, the notes as Markdown for the import, and pages drawn with their strokes.

Ink is stored the way the browser draws it: strokes in units of the page's width (so the same numbers fit
any zoom and screen), y measured in page widths too. Rendering here (PyMuPDF) is for the annotated PDF
download and for the pictures the reading model gets of pages that carry handwriting.
"""

from __future__ import annotations

import math
import re
import threading
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from PIL import Image

MAX_STROKES = 4000
MAX_POINTS_PER_STROKE = 20000
MAX_POINTS_PER_PAGE = 250_000
MAX_NOTES_CHARS = 200_000
MAX_PAGES = 600
RENDER_SIDE = 1600
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
HIGHLIGHT_OPACITY = 0.35

# MuPDF is not thread safe: every use goes through this lock (the extraction shares it).
LOCK = threading.Lock()


class InkError(ValueError):
    pass


def clean_ink(raw: Any) -> list[dict[str, Any]]:
    """Validate and normalise the strokes a client sends; raises InkError on anything malformed."""
    if not isinstance(raw, list):
        raise InkError("ink must be a list of strokes")
    if len(raw) > MAX_STROKES:
        raise InkError(f"too many strokes (max {MAX_STROKES})")
    out: list[dict[str, Any]] = []
    total = 0
    for s in raw:
        if not isinstance(s, dict) or s.get("t") not in ("pen", "hl"):
            raise InkError("bad stroke")
        color, width, pts = s.get("c"), s.get("w"), s.get("p")
        if not isinstance(color, str) or not _COLOR.match(color):
            raise InkError("bad stroke colour")
        if not isinstance(width, (int, float)) or isinstance(width, bool) or not 0 < width <= 0.2 or not math.isfinite(width):
            raise InkError("bad stroke width")
        if not isinstance(pts, list) or not pts or len(pts) % 3 or len(pts) > 3 * MAX_POINTS_PER_STROKE:
            raise InkError("bad stroke points")
        total += len(pts) // 3
        if total > MAX_POINTS_PER_PAGE:
            raise InkError("too much ink on this page")
        for v in pts:
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or not -2 <= v <= 8:
                raise InkError("bad stroke coordinate")
        out.append({"t": s["t"], "c": color.lower(), "w": round(float(width), 5), "p": [round(float(v), 4) for v in pts]})
    return out


def has_ink(ink: Any) -> bool:
    return bool(ink)


# --------------------------------------------------------------------------- the notes as Markdown for the import


def slide_number_before(pages: list[dict[str, Any]], index: int) -> int:
    """The last slide (PDF page number) at or before pages[index]; 0 when the page comes before all of them."""
    for p in reversed(pages[: index + 1]):
        if p.get("slide_page"):
            return int(p["slide_page"])
    return 0


def page_label(pages: list[dict[str, Any]], index: int) -> str:
    """How the notes and the pictures of a page are called in the import: «Slide 5» or «Pagina aggiunta dopo la slide 5»."""
    p = pages[index]
    if p.get("kind") == "slide":
        return f"Slide {p['slide_page']}"
    after = slide_number_before(pages, index)
    return f"Pagina aggiunta dopo la slide {after}" if after else "Pagina aggiunta all'inizio"


def notes_markdown(title: str, pages: list[dict[str, Any]]) -> str | None:
    """The typed notes of the lesson, in page order, under a heading per slide; None when nothing was typed."""
    sections = []
    for i, p in enumerate(pages):
        text = (p.get("notes") or "").strip()
        if text:
            sections.append(f"## {page_label(pages, i)}\n\n{text}")
    if not sections:
        return None
    return f"# {title.strip() or 'Lezione'}\n\n" + "\n\n".join(sections) + "\n"


# --------------------------------------------------------------------------- drawing


def _rgb(hex_color: str) -> tuple[float, float, float]:
    return tuple(int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def draw_strokes(page: fitz.Page, strokes: list[dict[str, Any]]) -> None:
    """Draw the strokes on a page (highlighters first, so pens stay readable above them)."""
    if not strokes:
        return
    rect = page.rect
    w_page = rect.width
    derot = page.derotation_matrix
    shape = page.new_shape()
    for kind in ("hl", "pen"):
        for s in strokes:
            if s["t"] != kind:
                continue
            pts = s["p"]
            n = len(pts) // 3
            if n == 0:
                continue
            width = max(0.2, s["w"] * w_page)
            color = _rgb(s["c"])
            points = [fitz.Point(rect.x0 + pts[i * 3] * w_page, rect.y0 + pts[i * 3 + 1] * w_page) * derot for i in range(n)]
            opacity = HIGHLIGHT_OPACITY if kind == "hl" else 1
            if n == 1:
                shape.draw_circle(points[0], width / 2)
                shape.finish(color=None, fill=color, fill_opacity=opacity)
            else:
                shape.draw_polyline(points)
                shape.finish(color=color, width=width, closePath=False, lineCap=0 if kind == "hl" else 1, lineJoin=1, stroke_opacity=opacity)
    shape.commit()


def _image(page: fitz.Page, side: int) -> Image.Image:
    r = page.rect
    s = side / max(r.width, r.height, 1.0)
    pix = page.get_pixmap(matrix=fitz.Matrix(s, s), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def render_slide(pdf_path: Path | str, slide_page: int, strokes: list[dict[str, Any]], side: int = RENDER_SIDE) -> Image.Image:
    """A slide with the strokes on it, as a picture."""
    with LOCK:
        doc = fitz.open(str(pdf_path))
        try:
            page = doc[slide_page - 1]
            draw_strokes(page, strokes)
            return _image(page, side)
        finally:
            doc.close()


def render_blank(ratio: float, strokes: list[dict[str, Any]], side: int = RENDER_SIDE) -> Image.Image:
    """A blank page (white, the given height/width) with the strokes on it, as a picture."""
    with LOCK:
        doc = fitz.open()
        try:
            page = doc.new_page(width=1000, height=1000 * max(0.2, min(ratio, 5.0)))
            draw_strokes(page, strokes)
            return _image(page, side)
        finally:
            doc.close()


def annotated_pdf(pdf_path: Path | str | None, pages: list[dict[str, Any]]) -> bytes:
    """The whole lesson as a PDF: slides and blank pages in order, with the strokes drawn on top."""
    with LOCK:
        out = fitz.open()
        src = fitz.open(str(pdf_path)) if pdf_path else None
        try:
            for p in pages:
                if p["kind"] == "slide" and src is not None and 1 <= (p.get("slide_page") or 0) <= src.page_count:
                    out.insert_pdf(src, from_page=p["slide_page"] - 1, to_page=p["slide_page"] - 1)
                    page = out[-1]
                else:
                    page = out.new_page(width=842, height=842 * max(0.2, min(float(p.get("ratio") or 0.5625), 5.0)))
                draw_strokes(page, p.get("ink") or [])
            if out.page_count == 0:
                out.new_page(width=842, height=595)
            return out.tobytes(garbage=3, deflate=True)
        finally:
            if src is not None:
                src.close()
            out.close()


def subset_pdf(pdf_path: Path | str, keep: list[int]) -> bytes:
    """The given pages (1-based, in this order) of a PDF as a new PDF: the slides of a lesson that were not deleted."""
    with LOCK:
        src = fitz.open(str(pdf_path))
        out = fitz.open()
        try:
            for n in keep:
                out.insert_pdf(src, from_page=n - 1, to_page=n - 1)
            return out.tobytes(garbage=3, deflate=True)
        finally:
            src.close()
            out.close()


def renumber_slides(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[int]]:
    """Slides that were deleted leave gaps in the PDF's numbering. Returns the pages with the remaining slides numbered 1, 2, 3…
    (what the import's text calls «Slide N») and the original PDF pages that remain, in order."""
    keep = [int(p["slide_page"]) for p in pages if p.get("kind") == "slide" and p.get("slide_page")]
    number = {old: i for i, old in enumerate(keep, start=1)}
    return [{**p, "slide_page": number[p["slide_page"]]} if p.get("kind") == "slide" and p.get("slide_page") else p for p in pages], keep


def pdf_page_ratios(path: Path | str) -> list[float]:
    """height / width of every page of a PDF (raises ValueError when it can't be used)."""
    with LOCK:
        try:
            doc = fitz.open(str(path))
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"PDF non leggibile: {e}") from e
        try:
            if doc.needs_pass:
                raise ValueError("il PDF è protetto da password")
            if doc.page_count == 0:
                raise ValueError("il PDF non ha pagine")
            if doc.page_count > MAX_PAGES:
                raise ValueError(f"il PDF ha troppe pagine (massimo {MAX_PAGES})")
            out = []
            for p in doc:
                r = p.rect
                out.append(round(r.height / r.width, 4) if r.width > 0 and r.height > 0 else 0.5625)
            return out
        finally:
            doc.close()
