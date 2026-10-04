"""Stage 1: local analysis (no cloud calls). CPU-bound; called via run_cpu().

* Pictures: which embedded images of a PDF are decoration (pdfextract does the rest), crops, thumbnails.
* Markdown: diagram code blocks and relative images extracted, then
  `pandoc --sandbox` to LaTeX (math preserved).
"""

from __future__ import annotations

import io
import re
import subprocess
from typing import Any

from PIL import Image

from ..services import blobs
from ..services.texttools import detect_language

MODEL_DPI = 150
CROP_DPI = 220
MAX_MODEL_SIDE = 2000
THUMB = 360

# Embedded images that are slide decoration (logos, crests, header/footer graphics), not content:
# small ones repeated on many pages, and small ones inside the header/footer bands.
FIGURE_MIN_AREA, FIGURE_MAX_AREA = 0.03, 0.85
DECOR_MAX_AREA = 0.12
REPEAT_MIN_PAGES, REPEAT_MIN_SHARE = 3, 0.4
MARGIN_MAX_AREA, HEADER_BAND, FOOTER_BAND = 0.08, 0.2, 0.85


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _jpeg(im: Image.Image, q: int = 85) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q, optimize=True)
    return buf.getvalue()


def _thumb(im: Image.Image) -> str:
    t = im.copy()
    t.thumbnail((THUMB, THUMB))
    return blobs.put_bytes(_jpeg(t, 75))


def _limit(im: Image.Image, side: int = MAX_MODEL_SIDE) -> Image.Image:
    if max(im.size) > side:
        im = im.copy()
        im.thumbnail((side, side), Image.LANCZOS)
    return im


def in_margin(bbox: list[float], area: float) -> bool:
    """A small image entirely inside the header or footer band of a page."""
    return area <= MARGIN_MAX_AREA and (bbox[3] <= HEADER_BAND or bbox[1] >= FOOTER_BAND)


def split_decoration(pages: list[list[dict[str, Any]]]) -> tuple[list[list[dict[str, Any]]], list[list[dict[str, Any]]]]:
    """Split each page's embedded images into (figures, skipped).

    Skipped: decoration (small images repeated across many pages, e.g. a university logo on every
    slide, or small images in the header/footer bands) and exact repeats of an image already kept
    on an earlier page (slide builds), so each picture becomes at most one figure.
    """
    on_pages: dict[str, set[int]] = {}
    for n, imgs in enumerate(pages):
        for im in imgs:
            if im.get("digest"):
                on_pages.setdefault(im["digest"], set()).add(n)
    min_pages = max(REPEAT_MIN_PAGES, int(REPEAT_MIN_SHARE * len(pages) + 0.999))
    first_page: dict[str, int] = {}
    keep: list[list[dict[str, Any]]] = []
    skip: list[list[dict[str, Any]]] = []
    for n, imgs in enumerate(pages):
        k: list[dict[str, Any]] = []
        s: list[dict[str, Any]] = []
        for im in imgs:
            d = im.get("digest")
            if d and im["area"] <= DECOR_MAX_AREA and len(on_pages[d]) >= min_pages:
                s.append({**im, "reason": f"decoration repeated on {len(on_pages[d])} pages"})
            elif in_margin(im["bbox"], im["area"]):
                s.append({**im, "reason": "decoration in the header/footer"})
            elif d and d in first_page:
                s.append({**im, "reason": f"same image as page {first_page[d] + 1}"})
            else:
                if d:
                    first_page[d] = n
                k.append(im)
        keep.append(k)
        skip.append(s)
    return keep, skip


def crop_image(blob: str, bbox: list[float]) -> tuple[str, int, int]:
    im = Image.open(io.BytesIO(blobs.read_bytes(blob))).convert("RGB")
    w, h = im.size
    x0, y0, x1, y1 = [max(0.0, min(1.0, v)) for v in bbox]
    pad = 0.01
    box = (int(max(0, x0 - pad) * w), int(max(0, y0 - pad) * h), int(min(1, x1 + pad) * w), int(min(1, y1 + pad) * h))
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        box = (0, 0, w, h)
    c = im.crop(box)
    return blobs.put_bytes(_png(c)), c.width, c.height


_FENCE_RE = re.compile(r"^(```|~~~)[ \t]*([\w+-]*)[^\n]*\n(.*?)^\1[ \t]*$", re.M | re.S)
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_BOX_CHARS = set("│─┌┐└┘├┤┬┴┼═║╔╗╚╝▶◀▲▼→←↑↓")
PLACEHOLDER = "LECTAFIGPLACEHOLDER"


def _looks_like_ascii_art(code: str) -> bool:
    if any(c in _BOX_CHARS for c in code):
        return True
    lines = code.splitlines()
    arrows = sum(1 for ln in lines if re.search(r"(-{2,}>|<-{2,}|=+>|\|\s*$|^\s*\+-{2,})", ln))
    return len(lines) >= 2 and arrows >= 2


def extract_markdown(md: str) -> tuple[str, list[dict[str, Any]]]:
    """Replace diagram code blocks and local images with placeholders.
    Returns (markdown, [{n, type: mermaid|ascii|image, code|path, alt}])."""
    found: list[dict[str, Any]] = []

    def fence(m: re.Match) -> str:
        lang, code = m.group(2).lower(), m.group(3)
        kind = None
        if lang == "mermaid":
            kind = "mermaid"
        elif lang in ("ascii", "asciiart", "text", "txt", "") and _looks_like_ascii_art(code):
            kind = "ascii"
        if not kind:
            return m.group(0)
        n = len(found) + 1
        found.append({"n": n, "type": kind, "code": code.strip("\n")})
        return f"\n\n{PLACEHOLDER}{n}\n\n"

    md = _FENCE_RE.sub(fence, md)

    def image(m: re.Match) -> str:
        alt, target = m.group(1), m.group(2)
        if re.match(r"^[a-z]+:", target):  # remote URLs are never fetched
            return f"(external image not included: {alt or target})"
        n = len(found) + 1
        found.append({"n": n, "type": "image", "path": target, "alt": alt})
        return f"\n\n{PLACEHOLDER}{n}\n\n"

    md = _IMG_RE.sub(image, md)
    return md, found


def pandoc_to_latex(md: str, timeout: int = 60) -> str:
    """Markdown → LaTeX with pandoc in sandbox mode (no file or network access)."""
    proc = subprocess.run(
        [
            "pandoc", "--sandbox", "--from", "markdown+tex_math_dollars+tex_math_single_backslash+raw_tex-auto_identifiers",
            "--to", "latex", "--wrap=preserve", "--no-highlight",
        ],
        input=md.encode("utf-8"),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("pandoc failed: " + proc.stderr.decode("utf-8", "replace")[:500])
    return proc.stdout.decode("utf-8")


def analyze_markdown(data: bytes) -> dict[str, Any]:
    md = data.decode("utf-8", errors="replace")
    stripped, found = extract_markdown(md)
    latex = pandoc_to_latex(stripped)
    title = None
    m = re.search(r"^#\s+(.+)$", md, re.M)
    if m:
        title = m.group(1).strip()
    return {"text": md, "stripped": stripped, "latex": latex, "blocks": found, "title": title, "language": detect_language(md)}
