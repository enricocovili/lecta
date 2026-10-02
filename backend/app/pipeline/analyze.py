"""Stage 1: local analysis (no cloud calls). CPU-bound; called via run_cpu().

* PDFs: text layer, embedded images, page classification heuristics,
  page renders at ~150 DPI for the models, higher-DPI crops for figures.
* Photos: EXIF rotation, HEIC conversion, page detection + perspective crop,
  deskew, contrast, downscale.
* Markdown: diagram code blocks and relative images extracted, then
  `pandoc --sandbox` to LaTeX (math preserved).
"""

from __future__ import annotations

import io
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

import cv2
import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageOps

from ..services import blobs
from ..services.texttools import detect_language

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pillow_heif = None

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


@dataclass
class PageInfo:
    page: int
    kind: str
    text: str
    width: int
    height: int
    image_blob: str
    preview_blob: str
    language: str
    heuristics: dict[str, Any]
    embedded: list[dict[str, Any]] = field(default_factory=list)  # [{bbox (0..1), area, digest}]
    skipped: list[dict[str, Any]] = field(default_factory=list)  # decoration/duplicates: [{bbox, area, digest, reason}]


# --------------------------------------------------------------------------- PDF


def classify_pdf_page(page: fitz.Page) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    rect = page.rect
    area = max(1.0, rect.width * rect.height)
    text = page.get_text("text") or ""
    chars = len(text.strip())
    landscape = rect.width > rect.height * 1.1
    images = []
    cov = 0.0
    try:
        for info in page.get_image_info(hashes=True, xrefs=True):
            b = fitz.Rect(info["bbox"]) & rect
            if b.is_empty:
                continue
            frac = (b.width * b.height) / area
            cov += frac
            digest = info.get("digest")
            images.append({"bbox": [b.x0 / rect.width, b.y0 / rect.height, b.x1 / rect.width, b.y1 / rect.height], "area": round(frac, 4),
                           "digest": digest.hex() if isinstance(digest, bytes | bytearray) else None})
    except Exception:
        pass
    ink = 0
    for a in page.annots() or []:
        if a.type[1] in ("Ink", "FreeText", "Line", "Square", "Circle", "Polygon", "PolyLine", "Highlight", "Underline", "StrikeOut"):
            ink += 1
    try:
        drawings = len(page.get_drawings())
    except Exception:
        drawings = 0
    h = {"chars": chars, "landscape": landscape, "image_coverage": round(min(cov, 1.0), 3), "ink_annotations": ink, "drawings": drawings}
    if chars < 40 and cov > 0.6:
        kind = "unknown"  # a scan: handwritten or typed → the vision model decides
    elif ink > 0 or (drawings > 150 and chars > 0):
        kind = "annotated_slide"
    elif landscape and chars > 0:
        kind = "slide"
    elif chars > 600:
        kind = "typed"
    elif chars > 0:
        kind = "slide" if landscape else "typed"
    else:
        kind = "unknown"
    figures = [i for i in images if FIGURE_MIN_AREA <= i["area"] <= FIGURE_MAX_AREA]
    return kind, h, figures


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


def analyze_pdf(data: bytes, label: str) -> list[PageInfo]:
    doc = fitz.open(stream=data, filetype="pdf")
    out = []
    try:
        if doc.needs_pass:
            raise ValueError("encrypted PDF")
        for i, page in enumerate(doc):
            kind, h, embedded = classify_pdf_page(page)
            pix = page.get_pixmap(dpi=MODEL_DPI, alpha=False)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            im = _limit(im)
            text = (page.get_text("text") or "").strip()
            out.append(
                PageInfo(
                    page=i + 1,
                    kind=kind,
                    text=text,
                    width=im.width,
                    height=im.height,
                    image_blob=blobs.put_bytes(_png(im)),
                    preview_blob=_thumb(im),
                    language=detect_language(text),
                    heuristics=h,
                    embedded=embedded,
                )
            )
    finally:
        doc.close()
    keep, skip = split_decoration([p.embedded for p in out])
    for p, k, sk in zip(out, keep, skip, strict=True):
        p.embedded, p.skipped = k, sk
    return out


def crop_pdf(data: bytes, page: int, bbox: list[float], dpi: int = CROP_DPI) -> tuple[str, int, int]:
    """Crop a region (fractions 0..1) of a PDF page at higher DPI."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        p = doc[page - 1]
        r = p.rect
        x0, y0, x1, y1 = bbox
        pad = 0.01
        clip = fitz.Rect(
            r.x0 + max(0, x0 - pad) * r.width, r.y0 + max(0, y0 - pad) * r.height,
            r.x0 + min(1, x1 + pad) * r.width, r.y0 + min(1, y1 + pad) * r.height,
        )
        pix = p.get_pixmap(dpi=dpi, clip=clip, alpha=False)
        im = _limit(Image.frombytes("RGB", (pix.width, pix.height), pix.samples), 2400)
        return blobs.put_bytes(_png(im)), im.width, im.height
    finally:
        doc.close()


# --------------------------------------------------------------------------- photos


def _order_quad(pts: np.ndarray) -> np.ndarray:
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype="float32")


def _find_page(bgr: np.ndarray) -> np.ndarray | None:
    h, w = bgr.shape[:2]
    scale = 900 / max(h, w)
    small = cv2.resize(bgr, (int(w * scale), int(h * scale))) if scale < 1 else bgr.copy()
    scale = min(scale, 1.0)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area = small.shape[0] * small.shape[1]
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        if cv2.contourArea(c) < 0.3 * area:
            break
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return _order_quad(approx.reshape(4, 2).astype("float32") / scale)
    return None


def _warp(bgr: np.ndarray, quad: np.ndarray) -> np.ndarray:
    tl, tr, br, bl = quad
    wa, wb = np.linalg.norm(br - bl), np.linalg.norm(tr - tl)
    ha, hb = np.linalg.norm(tr - br), np.linalg.norm(tl - bl)
    W, H = int(max(wa, wb)), int(max(ha, hb))
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(bgr, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _skew_angle(gray: np.ndarray) -> float:
    small = gray
    if max(gray.shape) > 1200:
        s = 1200 / max(gray.shape)
        small = cv2.resize(gray, (int(gray.shape[1] * s), int(gray.shape[0] * s)))
    thr = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    # Join characters of a line so the dominant direction is the text baseline.
    thr = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3)))
    lines = cv2.HoughLinesP(thr, 1, np.pi / 360, threshold=80, minLineLength=small.shape[1] // 4, maxLineGap=20)
    if lines is None:
        return 0.0
    angles = [np.degrees(np.arctan2(y2 - y1, x2 - x1)) for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4)]
    angles = [a for a in angles if abs(a) < 15]
    return float(np.median(angles)) if angles else 0.0


def preprocess_photo(data: bytes) -> dict[str, Any]:
    im = Image.open(io.BytesIO(data))
    ops = []
    exif_orientation = None
    try:
        exif_orientation = im.getexif().get(274)
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    if exif_orientation and exif_orientation != 1:
        ops.append(f"exif-rotate({exif_orientation})")
    if im.format in ("HEIF", "HEIC") or (pillow_heif and type(im).__name__.startswith("Heif")):
        ops.append("heic→png")
    im = im.convert("RGB")
    original = _limit(im, 2400)
    bgr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    quad = _find_page(bgr)
    if quad is not None:
        bgr = _warp(bgr, quad)
        ops.append("page-crop")
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    angle = _skew_angle(gray)
    if abs(angle) > 0.4:
        h, w = bgr.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        bgr = cv2.warpAffine(bgr, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        ops.append(f"deskew({angle:.1f}°)")
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    l_ch = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l_ch)
    bgr = cv2.cvtColor(cv2.merge((l_ch, a_ch, b_ch)), cv2.COLOR_LAB2BGR)
    ops.append("contrast")
    out = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    if max(out.size) > MAX_MODEL_SIDE:
        ops.append("downscale")
    out = _limit(out)
    # A real photograph (not a page of notes): colourful, few "paper" pixels.
    hsv = cv2.cvtColor(np.array(out), cv2.COLOR_RGB2HSV)
    colourful = float((hsv[:, :, 1] > 90).mean())
    paper = float((cv2.cvtColor(np.array(out), cv2.COLOR_RGB2GRAY) > 170).mean())
    return {
        "image_blob": blobs.put_bytes(_jpeg(out, 88)),
        "preview_blob": _thumb(out),
        "original_blob": blobs.put_bytes(_jpeg(original, 90)),
        "width": out.width,
        "height": out.height,
        "ops": ops,
        "looks_like_photo": colourful > 0.35 and paper < 0.3,
        "stats": {"colourful": round(colourful, 3), "paper": round(paper, 3)},
    }


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


def image_size(blob: str) -> tuple[int, int]:
    return Image.open(io.BytesIO(blobs.read_bytes(blob))).size


def render_pdf_png(pdf: bytes, dpi: int = 150) -> tuple[str, int, int]:
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        pix = doc[0].get_pixmap(dpi=dpi, alpha=False)
        im = _limit(Image.frombytes("RGB", (pix.width, pix.height), pix.samples), 2000)
        return blobs.put_bytes(_png(im)), im.width, im.height
    finally:
        doc.close()


# --------------------------------------------------------------------------- Markdown

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
