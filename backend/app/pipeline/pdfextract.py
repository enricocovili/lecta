"""Local PDF extraction (no AI): text with math hints, pictures and page routing.

For every page:
* text in reading order from the text layer, with light math hints (raised/lowered small spans
  become ^{…}/_{…}), `# ` heading hints and the page title; header/footer lines repeated on
  many pages (slide numbers, course name) are dropped;
* pictures: embedded images (minus decoration: logos, repeats, header/footer graphics) and
  vector drawings (plots, diagrams) found by clustering the drawing commands; each becomes an
  image file and a `[[IMG id]]` marker in the text where it sits; text inside a picture (its
  labels) is removed from the text;
* routing: the page goes to the text model ("text") or also needs its picture ("eyes": scans,
  garbled text layers, handwritten ink, display math, formulas stored as images) or is skipped
  (blank pages, beamer overlay steps that the next page repeats).

Only "eyes" pages are rendered; every page gets a small thumbnail.
PyMuPDF isn't thread-safe: extraction is serialised with a lock.
"""

from __future__ import annotations

import io
import re
import unicodedata
from collections import Counter
from typing import Any

import fitz  # PyMuPDF
from PIL import Image

from ..services import blobs
from ..services.lessons import LOCK as _LOCK  # MuPDF is not thread safe: one lock for every use of it
from ..services.texttools import detect_language
from .analyze import FIGURE_MAX_AREA, FIGURE_MIN_AREA, split_decoration


CROP_DPI = 200
RENDER_SIDE = 1600
THUMB_SIDE = 360
MAX_FIGURES_PER_PAGE = 4
MAX_DRAWINGS = 6000
VECTOR_MIN_AREA = 0.015
LABEL_MAX_CHARS, LABEL_REACH = 16, 0.06
DENSE_MATH_SPANS = 25  # pages with more math-font spans than this are read with their picture too
HEADER_BAND, FOOTER_BAND = 0.1, 0.9
REPEAT_SHARE, REPEAT_MIN = 0.4, 3
MATH_FONT_RE = re.compile(r"CMMI|CMSY|CMEX|MSAM|MSBM|LMMath|Math|STIX|Symbol|MTExtra|MT Extra|rsfs|EUFM|EUSM|esint", re.I)
CMEX_RE = re.compile(r"CMEX|Extension", re.I)  # big operators and delimiters (display math)
INK_ANNOTS = {"Ink", "FreeText", "Polygon", "PolyLine", "Line", "Square", "Circle", "Stamp"}


# --------------------------------------------------------------------------- helpers


def _frac(r: fitz.Rect, page: fitz.Rect) -> list[float]:
    w, h = max(page.width, 1.0), max(page.height, 1.0)
    return [
        round(max(0.0, min(1.0, (r.x0 - page.x0) / w)), 4),
        round(max(0.0, min(1.0, (r.y0 - page.y0) / h)), 4),
        round(max(0.0, min(1.0, (r.x1 - page.x0) / w)), 4),
        round(max(0.0, min(1.0, (r.y1 - page.y0) / h)), 4),
    ]


def _area(b: list[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inside(inner: fitz.Rect, outer: fitz.Rect, slack: float = 1.0) -> bool:
    return inner.x0 >= outer.x0 - slack and inner.y0 >= outer.y0 - slack and inner.x1 <= outer.x1 + slack and inner.y1 <= outer.y1 + slack


def _jpeg(im: Image.Image, q: int) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q)
    return buf.getvalue()


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _pix_image(pix: fitz.Pixmap) -> Image.Image:
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


_ACCENTS = {"`a": "à", "`e": "è", "`i": "ì", "`o": "ò", "`u": "ù", "`A": "À", "`E": "È", "`I": "Ì", "`O": "Ò", "`U": "Ù",
            "´a": "á", "´e": "é", "´i": "í", "´o": "ó", "´u": "ú", "´E": "É", "¨a": "ä", "¨o": "ö", "¨u": "ü", "¨e": "ë"}
_ACCENT_RE = re.compile(r"[`´¨] ?[aeiouAEIOU]")


def fix_accents(t: str) -> str:
    """pdfTeX with OT1 fonts puts accents as separate glyphs before the letter ("Universit`a")."""
    return _ACCENT_RE.sub(lambda m: _ACCENTS.get(m.group(0).replace(" ", ""), m.group(0)), t)


def _norm_line(t: str) -> str:
    """Key for spotting page furniture: exact text, except that mostly-numeric lines (slide numbers,
    "3 / 25") ignore their digits. "Esercizio 1" and "Esercizio 2" stay different."""
    t = re.sub(r"\s+", " ", t).strip().lower()
    if sum(c.isalpha() for c in t) <= 3:
        return re.sub(r"\d+", "#", t)
    return t


def garbage_score(text: str) -> float:
    """0..1: how much of a text layer looks like garbage (private-use glyphs, U+FFFD, controls,
    or "words" without vowels, typical of fonts without a Unicode mapping)."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    bad = sum(1 for c in chars if c == "�" or 0xE000 <= ord(c) <= 0xF8FF or unicodedata.category(c) in ("Cc", "Co", "Cn"))
    if bad / len(chars) > 0.05:
        return min(1.0, bad / len(chars) * 5)
    words = re.findall(r"[^\W\d_]{3,}", text)
    letters = sum(len(w) for w in words)
    if letters < 200:
        return 0.0
    vowelled = sum(len(w) for w in words if re.search(r"[aeiouyàèéìòùáíóúäöüâêîôûAEIOUY]", w))
    return max(0.0, 1.0 - (vowelled / letters) / 0.5) if vowelled / letters < 0.5 else 0.0


# --------------------------------------------------------------------------- text


def _baseline(s: dict[str, Any]) -> float:
    # Span origins are normalised when MuPDF joins pieces of a line; the box bottom minus the
    # descender is a better estimate of where the glyphs sit.
    return s["bbox"][3] - 0.22 * s["size"]


def _span_text(spans: list[dict[str, Any]]) -> tuple[str, int, bool]:
    """Join the spans of one line; raised/lowered smaller spans become ^{…}/_{…}."""
    spans = sorted(spans, key=lambda s: s["bbox"][0])
    sized = [s for s in spans if s["text"].strip()]
    if not sized:
        return "", 0, False
    main = max(s["size"] for s in sized)
    base_spans = [s for s in sized if s["size"] >= 0.9 * main] or sized
    baseline = sorted(_baseline(s) for s in base_spans)[len(base_spans) // 2]
    out = ""
    prev_x1 = None
    math = 0
    cmex = False
    for s in spans:
        t = s["text"]
        if not t:
            continue
        font = s.get("font") or ""
        if MATH_FONT_RE.search(font):
            math += 1
            if CMEX_RE.search(font):
                cmex = True
        small = s["size"] < 0.85 * main and bool(t.strip())
        dy = _baseline(s) - baseline
        if t.strip() and ((s.get("flags", 0) & 1) or (small and dy < -0.12 * main)):
            t = "^{" + t.strip() + "}"
        elif small and dy > 0.15 * main:
            t = "_{" + t.strip() + "}"
        x0 = s["bbox"][0]
        if prev_x1 is not None and out and not out.endswith(" ") and not t.startswith((" ", "^", "_")) and x0 - prev_x1 > 0.2 * s["size"]:
            out += " "
        out += t
        prev_x1 = s["bbox"][2]
    return out.rstrip(), math, cmex


def _raw_lines(page: fitz.Page) -> list[dict[str, Any]]:
    flags = (fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES) | fitz.TEXT_DEHYPHENATE
    d = page.get_text("dict", flags=flags, sort=True)
    out = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            # Invisible text (OCR layers, hidden tricks) is not what the page shows.
            spans = [s for s in ln.get("spans", []) if s.get("text") and s.get("alpha", 255) != 0]
            if spans:
                out.append({"bbox": fitz.Rect(ln["bbox"]), "spans": spans, "size": max(s["size"] for s in spans)})
    return out


def _merge_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join pieces of one visual line that the PDF stores separately (a superscript, a subscript,
    the text after it): they overlap vertically and follow each other horizontally."""
    merged: list[dict[str, Any]] = []
    for ln in sorted(lines, key=lambda x: (round(x["bbox"].y0), x["bbox"].x0)):
        target = None
        for m in reversed(merged[-12:]):
            a, b = m["bbox"], ln["bbox"]
            overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
            gap = max(b.x0 - a.x1, a.x0 - b.x1)  # whichever side the new piece is on
            if overlap >= 0.3 * min(a.height, b.height) and -1.0 <= gap <= 0.8 * max(m["size"], ln["size"]):
                target = m
                break
        if target is None:
            merged.append({"bbox": fitz.Rect(ln["bbox"]), "spans": list(ln["spans"]), "size": ln["size"]})
        else:
            target["spans"] += ln["spans"]
            target["bbox"] |= ln["bbox"]
            target["size"] = max(target["size"], ln["size"])
    merged.sort(key=lambda x: (round(x["bbox"].y0 / 3), x["bbox"].x0))
    return merged


def _lines(page: fitz.Page) -> list[dict[str, Any]]:
    out = []
    for ln in _merge_lines(_raw_lines(page)):
        text, math, cmex = _span_text(ln["spans"])
        text = fix_accents(text)
        if text.strip():
            out.append({"bbox": ln["bbox"], "text": text, "size": ln["size"], "math": math, "cmex": cmex,
                        "chars": len(text)})
    return out


def _median_size(lines: list[dict[str, Any]]) -> float:
    """Font size of the bulk of the text (weighted by characters)."""
    if not lines:
        return 10.0
    total = sum(ln["chars"] for ln in lines)
    acc = 0
    for ln in sorted(lines, key=lambda x: x["size"]):
        acc += ln["chars"]
        if acc >= total / 2:
            return ln["size"]
    return lines[-1]["size"]


# --------------------------------------------------------------------------- vector figures


def _is_axis_line(item: tuple) -> bool:
    if item[0] != "l":
        return False
    a, b = item[1], item[2]
    return abs(a.x - b.x) < 0.5 or abs(a.y - b.y) < 0.5


def _classify_cluster(rect: fitz.Rect, paths: list[dict[str, Any]], text_inside: int) -> str | None:
    """None if the cluster looks like a picture, else why it isn't one."""
    items = [it for p in paths for it in p.get("items", [])]
    if not items:
        return "empty"
    curves = sum(1 for it in items if it[0] == "c")
    lines = [it for it in items if it[0] == "l"]
    rects = sum(1 for it in items if it[0] in ("re", "qu"))
    if min(rect.width, rect.height) < 6:
        return "rule"
    if len(items) < 4 and not curves:
        return "rule or box"
    # A box (possibly rounded or shadowed, like beamer blocks) with text in it: only filled shapes, no strokes.
    if text_inside > 0 and all(p.get("type") == "f" for p in paths):
        return "text box"
    if len(paths) <= 6 and text_inside > 20 and all(p.get("fill") is not None for p in paths):
        return "text box"
    # A table: only axis-aligned lines / rects, many of them spanning the whole cluster.
    if not curves and lines and all(_is_axis_line(it) for it in lines) and text_inside > 0:
        long = sum(1 for it in lines if abs(it[1].x - it[2].x) > 0.8 * rect.width or abs(it[1].y - it[2].y) > 0.8 * rect.height)
        if long >= 0.5 * len(lines) or rects == len(items) - len(lines):
            return "table grid"
    return None


def _drawing_sig(dr: dict[str, Any], prect: fitz.Rect) -> tuple:
    r = dr["rect"]
    w, h = max(prect.width, 1.0), max(prect.height, 1.0)
    fill = tuple(round(c, 2) for c in dr.get("fill") or ()) or None
    return (dr.get("type"), round(r.x0 / w, 3), round(r.y0 / h, 3), round(r.x1 / w, 3), round(r.y1 / h, 3), fill, len(dr.get("items") or ()))


def _vector_regions(page: fitz.Page, prect: fitz.Rect, lines: list[dict[str, Any]], drawings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    info: dict[str, Any] = {"drawings": len(drawings), "curves": 0}
    if not drawings:
        return [], info
    if len(drawings) > MAX_DRAWINGS:
        # Very dense artwork (maps, scatter plots with thousands of points): one region.
        r = fitz.Rect()
        for dr in drawings:
            r |= dr["rect"]
        clusters = [r & prect]
    else:
        try:
            clusters = page.cluster_drawings(drawings=drawings)
        except Exception:  # noqa: BLE001
            clusters = []
    out = []
    for c in clusters:
        c = fitz.Rect(c) & prect
        if c.is_empty:
            continue
        b = _frac(c, prect)
        a = _area(b)
        paths = [dr for dr in drawings if _inside(dr["rect"], c, 2.0)]
        curves = sum(1 for p in paths for it in p.get("items", []) if it[0] == "c")
        info["curves"] += curves
        text_in = [ln for ln in lines if _inside(ln["bbox"], c, 2.0)]
        chars_in = sum(len(ln["text"]) for ln in text_in)
        entry = {"bbox": b, "area": round(a, 4), "paths": len(paths), "curves": curves, "chars_inside": chars_in,
                 "sig": (tuple(round(v, 2) for v in b), len(paths))}
        if a < VECTOR_MIN_AREA or (b[2] - b[0]) < 0.08 or (b[3] - b[1]) < 0.04 or a > FIGURE_MAX_AREA:
            entry["reason"] = "page frame/background" if a > FIGURE_MAX_AREA else "too small"
        elif b[3] <= HEADER_BAND + 0.05 or b[1] >= FOOTER_BAND - 0.02:
            entry["reason"] = "header/footer decoration"
        else:
            entry["reason"] = _classify_cluster(c, paths, chars_in)
        out.append(entry)
    return out, info


# --------------------------------------------------------------------------- per page scan


def _scan(page: fitz.Page) -> dict[str, Any]:
    try:
        if page.rotation:
            page.remove_rotation()
    except Exception:  # noqa: BLE001
        pass
    prect = page.rect
    lines = _lines(page)
    images = []
    try:
        for info in page.get_image_info(hashes=True, xrefs=True):
            r = fitz.Rect(info["bbox"]) & prect
            if r.is_empty:
                continue
            b = _frac(r, prect)
            digest = info.get("digest")
            clipped = not _inside(fitz.Rect(info["bbox"]), prect, 1.0)
            t = info.get("transform") or (1, 0, 0, 1, 0, 0)
            if min(info.get("width") or 0, info.get("height") or 0) <= 4 or (b[2] - b[0]) > 12 * max(b[3] - b[1], 1e-4) \
                    or (b[3] - b[1]) > 12 * max(b[2] - b[0], 1e-4):
                continue  # gradients/shadings and thin bars of the slide theme
            images.append({"bbox": b, "area": round(_area(b), 4), "digest": digest.hex() if isinstance(digest, bytes | bytearray) else None,
                           "xref": info.get("xref") or 0, "axis": abs(t[1]) < 1e-6 and abs(t[2]) < 1e-6 and t[0] > 0 and t[3] > 0,
                           "clipped": clipped})
    except Exception:  # noqa: BLE001
        pass
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        drawings = []
    ink = 0
    notes = []
    for a in page.annots() or []:
        if a.type[1] in INK_ANNOTS:
            ink += 1
        content = (a.info or {}).get("content")
        if content and a.type[1] in ("Text", "FreeText"):
            notes.append(content.strip())
    return {"page": page, "rect": prect, "lines": lines, "images": images, "drawings": drawings, "ink": ink, "notes": notes,
            "landscape": prect.width > prect.height * 1.1}


def _title(lines: list[dict[str, Any]], prect: fitz.Rect, median: float, landscape: bool) -> str | None:
    top = [ln for ln in lines if ln["bbox"].y1 <= prect.y0 + 0.3 * prect.height]
    if not top:
        return None
    best = max(top, key=lambda ln: ln["size"])
    in_title_bar = landscape and best["bbox"].y1 <= prect.y0 + 0.18 * prect.height and len(best["text"]) < 100
    if best["size"] >= 1.08 * median or in_title_bar:
        return re.sub(r"[\^_]\{([^}]*)\}", r"\1", best["text"]).strip()[:200] or None
    return None


# --------------------------------------------------------------------------- crops


def _crop(doc: fitz.Document, page: fitz.Page, bbox: list[float], xref: int | None) -> tuple[str, str, int, int]:
    """(blob, ext, width, height) of a picture: the embedded image itself when that is safe, else a render."""
    if xref:
        try:
            x = doc.extract_image(xref)
            ext = (x.get("ext") or "").lower()
            data = x.get("image") or b""
            if ext in ("png", "jpeg", "jpg") and not x.get("smask") and len(data) < 8 * 1024 * 1024:
                im = Image.open(io.BytesIO(data))
                if im.mode in ("RGB", "L", "P", "RGBA", "LA") and max(im.size) >= 64:
                    return blobs.put_bytes(data), "jpg" if ext in ("jpeg", "jpg") else "png", im.width, im.height
        except Exception:  # noqa: BLE001
            pass
    r = page.rect
    x0, y0, x1, y1 = bbox
    pad = 0.008
    clip = fitz.Rect(r.x0 + max(0, x0 - pad) * r.width, r.y0 + max(0, y0 - pad) * r.height,
                     r.x0 + min(1, x1 + pad) * r.width, r.y0 + min(1, y1 + pad) * r.height)
    pix = page.get_pixmap(dpi=CROP_DPI, clip=clip, alpha=False)
    im = _pix_image(pix)
    if max(im.size) > 2400:
        im.thumbnail((2400, 2400), Image.LANCZOS)
    return blobs.put_bytes(_png(im)), "png", im.width, im.height


def _render(page: fitz.Page, side: int) -> Image.Image:
    r = page.rect
    s = side / max(r.width, r.height, 1.0)
    return _pix_image(page.get_pixmap(matrix=fitz.Matrix(s, s), alpha=False))


# --------------------------------------------------------------------------- document


def extract_pdf(path: str, prefix: str) -> dict[str, Any]:
    """Extract one PDF. Picture ids are `<prefix>p<page>i<n>`. Returns a JSON-able dict."""
    with _LOCK:
        return _extract(path, prefix)


def _extract(path: str, prefix: str) -> dict[str, Any]:
    doc = fitz.open(path)
    try:
        if doc.needs_pass:
            raise ValueError("encrypted PDF")
        meta_title = ((doc.metadata or {}).get("title") or "").strip() or None
        scans = [_scan(p) for p in doc]
        n = len(scans)
        min_rep = max(REPEAT_MIN, int(REPEAT_SHARE * n + 0.999))
        toc_titles: dict[int, str] = {}
        try:
            for _level, t, pno in doc.get_toc(simple=True):
                if 1 <= pno <= n and t.strip():
                    toc_titles.setdefault(pno - 1, t.strip())
        except Exception:  # noqa: BLE001
            pass

        # Drawings of the slide theme (bars, frames, shadings) repeat on many pages: not content.
        dsig: Counter[tuple] = Counter()
        for sc in scans:
            dsig.update({_drawing_sig(dr, sc["rect"]) for dr in sc["drawings"]})
        for sc in scans:
            content = [dr for dr in sc["drawings"] if n < REPEAT_MIN or dsig[_drawing_sig(dr, sc["rect"])] < min_rep]
            sc["vectors"], sc["vinfo"] = _vector_regions(sc["page"], sc["rect"], sc["lines"], content)
            sc["drawings"] = None

        # Header/footer lines repeated on many pages (course name, slide numbers).
        band_lines: Counter[str] = Counter()
        for sc in scans:
            seen = set()
            for ln in sc["lines"]:
                y = (ln["bbox"].y0 + ln["bbox"].y1) / 2
                if y < sc["rect"].y0 + HEADER_BAND * sc["rect"].height or y > sc["rect"].y0 + FOOTER_BAND * sc["rect"].height:
                    seen.add(_norm_line(ln["text"]))
            band_lines.update(seen)
        repeated_lines = {t for t, c in band_lines.items() if c >= min_rep and n >= REPEAT_MIN}
        vec_sigs: Counter[Any] = Counter(v["sig"] for sc in scans for v in {v["sig"]: v for v in sc["vectors"]}.values())

        for sc in scans:
            sc["lines"] = [ln for ln in sc["lines"] if _norm_line(ln["text"]) not in repeated_lines or not (
                (ln["bbox"].y0 + ln["bbox"].y1) / 2 < sc["rect"].y0 + HEADER_BAND * sc["rect"].height
                or (ln["bbox"].y0 + ln["bbox"].y1) / 2 > sc["rect"].y0 + FOOTER_BAND * sc["rect"].height)]
            sc["median"] = _median_size(sc["lines"])
            sc["title"] = toc_titles.get(scans.index(sc)) or _title(sc["lines"], sc["rect"], sc["median"], sc["landscape"])
            sc["plain"] = [_norm_line(ln["text"]) for ln in sc["lines"]]

        # Beamer overlays: a page whose content the next page repeats (same title, subset) is a build step.
        overlay = [False] * n
        for i in range(n - 1):
            a, b = scans[i], scans[i + 1]
            if a["plain"] and a["title"] and a["title"] == b["title"] and set(a["plain"]) <= set(b["plain"]) and len(a["plain"]) < len(b["plain"]) + 1:
                digests_a = {im["digest"] for im in a["images"] if im["digest"]}
                digests_b = {im["digest"] for im in b["images"] if im["digest"]}
                if digests_a <= digests_b and not a["ink"]:
                    overlay[i] = True

        live = [i for i in range(n) if not overlay[i]]
        keep, skip = split_decoration([scans[i]["images"] for i in live])
        kept_images = {i: k for i, k in zip(live, keep, strict=True)}
        skipped_images = {i: s for i, s in zip(live, skip, strict=True)}

        pages: list[dict[str, Any]] = []
        for i, sc in enumerate(scans):
            page = doc[i]
            prect = sc["rect"]
            chars = sum(len(ln["text"]) for ln in sc["lines"])
            thumb = _render(page, THUMB_SIDE)
            out: dict[str, Any] = {"page": i + 1, "title": sc["title"], "route": "text", "why": [], "pictures": [], "skipped": [],
                                   "preview_blob": blobs.put_bytes(_jpeg(thumb, 70)), "render_blob": None, "width": None, "height": None,
                                   "landscape": sc["landscape"]}
            if overlay[i]:
                out.update(route="skip", why=["overlay step repeated by the next page"], text="", language="und")
                pages.append(out)
                continue

            # Pictures: embedded images and vector regions.
            pics: list[dict[str, Any]] = []
            for im in kept_images.get(i, []):
                if FIGURE_MIN_AREA <= im["area"] <= FIGURE_MAX_AREA:
                    pics.append({"bbox": im["bbox"], "origin": "image", "xref": im["xref"] if im["axis"] and not im["clipped"] else None,
                                 "area": im["area"]})
            formula_images = [im for im in kept_images.get(i, []) if im["area"] < FIGURE_MIN_AREA
                              and (im["bbox"][2] - im["bbox"][0]) >= 0.05 and (im["bbox"][3] - im["bbox"][1]) >= 0.015
                              and (im["bbox"][2] - im["bbox"][0]) > 2.5 * (im["bbox"][3] - im["bbox"][1])]
            ink_over_text = False
            for v in sc["vectors"]:
                if v.get("reason"):
                    out["skipped"].append({"bbox": v["bbox"], "reason": v["reason"], "origin": "vector"})
                    continue
                if vec_sigs[v["sig"]] >= min_rep:
                    out["skipped"].append({"bbox": v["bbox"], "reason": "repeated on many pages", "origin": "vector"})
                    continue
                if v["chars_inside"] > max(200, 0.4 * chars):
                    # Strokes over most of the text: handwriting drawn on the slide, not a picture.
                    if v["curves"] > 30:
                        ink_over_text = True
                    out["skipped"].append({"bbox": v["bbox"], "reason": "covers the page text", "origin": "vector"})
                    continue
                # Merge with an overlapping picture (a plot drawn over an image, or labels around it).
                merged = False
                for p in pics:
                    ib = [max(p["bbox"][0], v["bbox"][0]), max(p["bbox"][1], v["bbox"][1]), min(p["bbox"][2], v["bbox"][2]), min(p["bbox"][3], v["bbox"][3])]
                    if _area(ib) > 0.3 * min(_area(p["bbox"]), _area(v["bbox"])):
                        p["bbox"] = [min(p["bbox"][0], v["bbox"][0]), min(p["bbox"][1], v["bbox"][1]),
                                     max(p["bbox"][2], v["bbox"][2]), max(p["bbox"][3], v["bbox"][3])]
                        p["origin"], p["xref"] = "vector", None
                        merged = True
                        break
                if not merged:
                    pics.append({"bbox": v["bbox"], "origin": "vector", "xref": None, "area": v["area"]})
            for s in skipped_images.get(i, []):
                out["skipped"].append({"bbox": s["bbox"], "reason": s.get("reason"), "origin": "image"})
            pics = sorted(pics, key=lambda p: -_area(p["bbox"]))[:MAX_FIGURES_PER_PAGE]

            # Text inside a picture (axis labels, node names) belongs to the picture; short labels
            # just outside a vector drawing (tick labels, axis names) too.
            lines = sc["lines"]
            vec = [p for p in pics if p["origin"] == "vector"]
            orig = {id(p): list(p["bbox"]) for p in vec}
            for ln in lines:
                if len(ln["text"]) > LABEL_MAX_CHARS or ln["text"].startswith("# "):
                    continue
                lb = _frac(ln["bbox"], prect)
                best, best_d = None, LABEL_REACH
                for p in vec:
                    bb = orig[id(p)]
                    dx = max(bb[0] - lb[2], lb[0] - bb[2], 0.0)
                    dy = max(bb[1] - lb[3], lb[1] - bb[3], 0.0)
                    if (dx == 0 or dy == 0) and max(dx, dy) <= best_d and max(dx, dy) > 0:
                        best, best_d = p, max(dx, dy)
                if best is not None:
                    bb = best["bbox"]
                    best["bbox"] = [min(bb[0], lb[0]), min(bb[1], lb[1]), max(bb[2], lb[2]), max(bb[3], lb[3])]
            regions = []
            for k, p in enumerate(sorted(pics, key=lambda p: (p["bbox"][1], p["bbox"][0])), start=1):
                p["id"] = f"{prefix}p{i + 1}i{k}"
                g = 0.015
                r = fitz.Rect(prect.x0 + (p["bbox"][0] - g) * prect.width, prect.y0 + (p["bbox"][1] - g) * prect.height,
                              prect.x0 + (p["bbox"][2] + g) * prect.width, prect.y0 + (p["bbox"][3] + g) * prect.height)
                regions.append((r, p))
            body_lines = [ln for ln in lines if not any(_inside(ln["bbox"], r, 1.0) for r, _ in regions)]

            # Assemble the text with heading hints and picture markers at their vertical position.
            parts: list[str] = []
            pending = sorted(regions, key=lambda rp: rp[0].y0)
            for ln in body_lines:
                while pending and pending[0][0].y0 <= ln["bbox"].y0:
                    parts.append(f"[[IMG {pending.pop(0)[1]['id']}]]")
                prefix_h = "# " if ln["size"] >= 1.3 * sc["median"] and len(ln["text"]) < 120 else ""
                parts.append(prefix_h + ln["text"])
            parts += [f"[[IMG {rp[1]['id']}]]" for rp in pending]
            if sc["notes"]:
                parts.append("(notes on the page: " + " | ".join(sc["notes"]) + ")")
            text = "\n".join(parts).strip()
            out["text"] = text
            out["language"] = detect_language(text)
            math_spans = sum(ln["math"] for ln in body_lines)
            cmex = any(ln["cmex"] for ln in body_lines)
            out["math"] = {"spans": math_spans, "display": cmex}

            # Routing.
            img_cover = sum(im["area"] for im in kept_images.get(i, [])) + sum(s["area"] for s in skipped_images.get(i, []) if "repeated" not in (s.get("reason") or ""))
            why = []
            if chars < 40 and img_cover > 0.5:
                why.append("scan")
            elif chars < 40 and sc["vinfo"].get("drawings", 0) > 200:
                why.append("text drawn as shapes")
            if chars >= 40 and garbage_score(text) > 0.3:
                why.append("garbled text layer")
            if sc["ink"] or ink_over_text:
                why.append("handwritten annotations")
            if cmex or math_spans >= DENSE_MATH_SPANS:
                why.append("display math")
            if formula_images:
                why.append("formulas stored as images")
            if chars < 5 and not pics and not why:
                out.update(route="skip", why=["blank page"])
                pages.append(out)
                continue
            if why:
                out["route"], out["why"] = "eyes", why
                im = _render(page, RENDER_SIDE)
                out["render_blob"], out["width"], out["height"] = blobs.put_bytes(_jpeg(im, 85)), im.width, im.height

            for p in pics:
                blob, ext, w, h = _crop(doc, page, p["bbox"], p.get("xref"))
                out["pictures"].append({"id": p["id"], "bbox": p["bbox"], "origin": p["origin"], "blob": blob, "ext": ext, "width": w, "height": h})
            pages.append(out)
        return {"pages": pages, "meta_title": meta_title}
    finally:
        doc.close()
