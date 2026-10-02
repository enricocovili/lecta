"""Local PDF extraction: text with math hints, pictures (embedded and vector), page routing."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import fitz
from PIL import Image

from app.pipeline import pdfextract
from app.services import blobs

from .fixtures import block_diagram, slides_pdf

W, H = 842, 595


def _extract(doc: fitz.Document) -> list[dict]:
    path = Path(tempfile.mkdtemp()) / "x.pdf"
    doc.save(str(path))
    return pdfextract.extract_pdf(str(path), "f1")["pages"]


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_slides_text_and_embedded_picture():
    path = Path(tempfile.mkdtemp()) / "s.pdf"
    path.write_bytes(slides_pdf())
    pages = pdfextract.extract_pdf(str(path), "f1")["pages"]
    assert [p["route"] for p in pages] == ["text", "text", "text"]
    assert pages[0]["title"] == "Sistemi LTI" and pages[0]["text"].startswith("# Sistemi LTI")
    assert not pages[0]["pictures"] and len(pages[1]["pictures"]) == 1
    pic = pages[1]["pictures"][0]
    assert pic["origin"] == "image" and pic["id"] == "f1p2i1" and f"[[IMG {pic['id']}]]" in pages[1]["text"]
    assert pic["width"] >= 900 and blobs.exists(pic["blob"])  # the embedded image itself, not a low-res render
    # Text pages aren't rendered (only a small thumbnail each).
    assert all(p["render_blob"] is None and p["preview_blob"] for p in pages)


def test_superscripts_and_subscripts():
    doc = fitz.open()
    p = doc.new_page(width=W, height=H)
    p.insert_text((50, 200), "E = mc", fontsize=20)
    p.insert_text((113, 190), "2", fontsize=12)
    p.insert_text((50, 260), "x", fontsize=20)
    p.insert_text((62, 266), "i", fontsize=12)
    text = _extract(doc)[0]["text"]
    assert "E = mc^{2}" in text and "x_{i}" in text


def test_vector_diagram_becomes_a_picture_with_its_labels():
    doc = fitz.open()
    p = doc.new_page(width=W, height=H)
    p.insert_text((60, 80), "Schema", fontsize=30)
    p.insert_text((60, 130), "Il sistema trasforma l'ingresso nell'uscita.", fontsize=16)
    for x in (100, 350, 600):
        p.draw_rect(fitz.Rect(x, 250, x + 140, 330), color=(0, 0, 0), width=1.5)
    p.insert_text((130, 295), "Ingresso", fontsize=14)
    p.insert_text((390, 295), "Filtro", fontsize=14)
    p.insert_text((630, 295), "Uscita", fontsize=14)
    for x0, x1 in ((240, 350), (490, 600)):
        p.draw_line((x0, 290), (x1, 290), color=(0, 0, 0), width=1.5)
        p.draw_polyline([(x1 - 10, 284), (x1, 290), (x1 - 10, 296)], color=(0, 0, 0), fill=(0, 0, 0))
    page = _extract(doc)[0]
    assert len(page["pictures"]) == 1, page["skipped"]
    pic = page["pictures"][0]
    assert pic["origin"] == "vector"
    assert "Ingresso" not in page["text"] and "Filtro" not in page["text"]  # labels belong to the picture
    assert f"[[IMG {pic['id']}]]" in page["text"] and "trasforma" in page["text"]
    assert page["text"].index("trasforma") < page["text"].index("[[IMG")


def test_text_boxes_tables_and_theme_graphics_are_not_pictures():
    doc = fitz.open()
    for n in range(5):
        p = doc.new_page(width=W, height=H)
        # Theme: a coloured title bar and a footer line on every slide.
        p.draw_rect(fitz.Rect(0, 0, W, 60), color=None, fill=(0.2, 0.3, 0.6))
        p.draw_rect(fitz.Rect(0, H - 25, W, H), color=None, fill=(0.2, 0.3, 0.6))
        p.insert_text((30, 40), f"Argomento {n + 1}", fontsize=24, color=(1, 1, 1))
        p.insert_text((30, H - 8), "Corso di Segnali - Università", fontsize=9)
        p.insert_text((W - 60, H - 8), f"{n + 1} / 5", fontsize=9)
        if n == 1:  # a block with text in it (beamer style)
            p.draw_rect(fitz.Rect(60, 120, 780, 220), color=None, fill=(0.9, 0.9, 1.0))
            p.draw_rect(fitz.Rect(60, 120, 780, 145), color=None, fill=(0.3, 0.3, 0.8))
            p.insert_text((70, 138), "Definizione", fontsize=14)
            p.insert_text((70, 180), "Un segnale è a energia finita se la sua energia è finita.", fontsize=14)
        if n == 2:  # a table grid
            for y in (150, 190, 230, 270):
                p.draw_line((100, y), (500, y), color=(0, 0, 0))
            for x in (100, 300, 500):
                p.draw_line((x, 150), (x, 270), color=(0, 0, 0))
            for r, y in enumerate((175, 215, 255)):
                p.insert_text((120, y), f"voce {r}", fontsize=12)
                p.insert_text((320, y), f"valore {r}", fontsize=12)
    pages = _extract(doc)
    assert all(not p["pictures"] for p in pages), [(p["page"], p["skipped"]) for p in pages]
    assert "Definizione" in pages[1]["text"] and "energia finita" in pages[1]["text"]
    assert "voce 1" in pages[2]["text"] and "valore 2" in pages[2]["text"]
    # Footer lines repeated on every slide are dropped; the titles are kept.
    assert all("Università" not in p["text"] and "/ 5" not in p["text"] for p in pages)
    assert [p["title"] for p in pages] == [f"Argomento {n + 1}" for n in range(5)]


def test_logos_and_repeated_images_are_not_pictures():
    logo = _png(Image.new("RGB", (200, 200), (180, 20, 40)))
    crest = _png(Image.new("RGB", (300, 120), (20, 40, 180)))
    diagram = _png(block_diagram())
    other = _png(block_diagram(700, 500))
    doc = fitz.open()
    for n in range(5):
        p = doc.new_page(width=W, height=H)
        p.insert_text((60, 300), f"Slide {n + 1}", fontsize=24)
        p.insert_image(fitz.Rect(680, 220, 830, 370), stream=logo)  # same logo mid-right on every slide
        if n == 0:
            p.insert_image(fitz.Rect(20, 10, 220, 90), stream=crest)  # header crest on the title slide
        if n in (1, 2):
            p.insert_image(fitz.Rect(120, 150, 620, 400), stream=diagram)  # the same picture twice
        if n == 3:
            p.insert_image(fitz.Rect(120, 150, 620, 500), stream=other)
    pages = _extract(doc)
    assert [len(p["pictures"]) for p in pages] == [0, 1, 0, 1, 0]
    reasons = [s["reason"] for p in pages for s in p["skipped"] if s["origin"] == "image"]
    assert sum("repeated on 5 pages" in r for r in reasons) == 5
    assert any("header/footer" in r for r in reasons)
    assert any("same image as page 2" in r for r in reasons)


def test_routing_scans_ink_and_overlays():
    doc = fitz.open()
    scan = _png(Image.new("RGB", (1200, 850), (245, 245, 240)))
    p1 = doc.new_page(width=W, height=H)
    p1.insert_image(fitz.Rect(0, 0, W, H), stream=scan)  # a scanned page: no text layer
    p2 = doc.new_page(width=W, height=H)
    p2.insert_text((60, 80), "Annotata", fontsize=28)
    p2.insert_text((60, 150), "Testo stampato della slide.", fontsize=16)
    p2.add_ink_annot([[(100, 300), (200, 320), (300, 310)]])
    # A beamer overlay: page 3 is a step that page 4 repeats and completes.
    for extra in (False, True):
        p = doc.new_page(width=W, height=H)
        p.insert_text((60, 80), "Elenco", fontsize=28)
        p.insert_text((60, 150), "- primo punto", fontsize=16)
        if extra:
            p.insert_text((60, 190), "- secondo punto", fontsize=16)
    doc.new_page(width=W, height=H)  # blank
    pages = _extract(doc)
    assert pages[0]["route"] == "eyes" and "scan" in pages[0]["why"] and pages[0]["render_blob"]
    assert pages[1]["route"] == "eyes" and "handwritten annotations" in pages[1]["why"]
    assert pages[2]["route"] == "skip" and pages[3]["route"] == "text" and "secondo punto" in pages[3]["text"]
    assert pages[4]["route"] == "skip"


def test_garbage_text_layer_is_detected():
    assert pdfextract.garbage_score("Il teorema fondamentale del calcolo integrale collega derivate e integrali. " * 5) == 0
    assert pdfextract.garbage_score("  " * 30) > 0.3
    assert pdfextract.garbage_score("xzqwrt pltkrm bdfghjk " * 20) > 0.3
    assert pdfextract.fix_accents("Universit`a e perch´e") == "Università e perché"


def test_strip_nul_cleans_nested_values():
    from app.services.texttools import strip_nul

    items = [{"text": "Metodo\x00 del gradiente", "meta": {"why": ["a\x00b"], "n": 3, "t": None}}]
    assert strip_nul(items) == [{"text": "Metodo del gradiente", "meta": {"why": ["ab"], "n": 3, "t": None}}]
