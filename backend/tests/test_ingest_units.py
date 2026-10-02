"""Extraction building blocks: unpacking safety, photos, Markdown (PDFs: test_pdfextract.py)."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from app.pipeline import analyze
from app.pipeline.unpack import UnpackError, unpack_zip
from app.services import blobs

from .fixtures import make_all


@pytest.fixture(scope="module")
def fx():
    d = Path(tempfile.mkdtemp(prefix="lecta-fx-"))
    return make_all(d)


def test_zip_members_are_sniffed_and_junk_skipped(fx, tmp_path):
    members = unpack_zip(fx["mixed.zip"], tmp_path / "out", max_members=100, max_total_bytes=10**9, max_ratio=200)
    by_name = {m.name: m for m in members}
    assert by_name["slides.pdf"].kind == "pdf" and by_name["slides.pdf"].folder == "Lezione 3 - Sistemi LTI"
    assert by_name["appunti.jpg"].kind == "image"
    assert by_name["note.md"].kind == "markdown"
    assert by_name["schema.png"].folder == "Lezione 3 - Sistemi LTI/img"
    assert by_name["._slides.pdf"].kind == "junk" and by_name[".DS_Store"].kind == "junk"
    assert by_name["setup.exe"].status == "unsupported"
    # Extracted files never escape the destination.
    for m in members:
        if m.path:
            assert str(m.path.resolve()).startswith(str((tmp_path / "out").resolve()))


def test_zip_slip_is_blocked(fx, tmp_path):
    members = unpack_zip(fx["zipslip.zip"], tmp_path / "o", max_members=100, max_total_bytes=10**9, max_ratio=200)
    assert all(m.path is None for m in members if "evil" in m.name)
    assert not (tmp_path / "evil.txt").exists() and not Path("/abs/evil2.txt").exists()
    assert any(m.name == "fine.md" and m.path for m in members)


def test_zip_bomb_is_refused(fx, tmp_path):
    with pytest.raises(UnpackError):
        unpack_zip(fx["zipbomb.zip"], tmp_path / "b", max_members=100, max_total_bytes=32 * 1024 * 1024, max_ratio=200)
    with pytest.raises(UnpackError):
        unpack_zip(fx["zipbomb.zip"], tmp_path / "c", max_members=100, max_total_bytes=10**10, max_ratio=100)


def test_photo_preprocessing(fx):
    res = analyze.preprocess_photo(fx["appunti.jpg"].read_bytes())
    assert any(op.startswith("exif-rotate") for op in res["ops"])
    assert "page-crop" in res["ops"]
    im = Image.open(io.BytesIO(blobs.read_bytes(res["image_blob"])))
    assert im.height > im.width  # upright portrait page after EXIF rotation + crop
    assert max(im.size) <= analyze.MAX_MODEL_SIDE
    assert not res["looks_like_photo"]


def test_heic_is_converted(fx):
    if "appunti.heic" not in fx:
        pytest.skip("no HEIC encoder")
    res = analyze.preprocess_photo(fx["appunti.heic"].read_bytes())
    im = Image.open(io.BytesIO(blobs.read_bytes(res["image_blob"])))
    assert im.format == "JPEG"


def test_markdown_extraction_and_pandoc(fx):
    r = analyze.analyze_markdown(fx["note.md"].read_bytes())
    types = [b["type"] for b in r["blocks"]]
    assert types == ["mermaid", "ascii", "image"]
    assert r["blocks"][2]["path"] == "img/schema.png"
    assert "\\int_{-\\infty}^{+\\infty}" in r["latex"]  # display math preserved
    assert "\\(T[a x_1 + b x_2]" in r["latex"] or "$T[a x_1" in r["latex"]
    assert analyze.PLACEHOLDER + "1" in r["latex"] and "graph LR" not in r["latex"]
    assert r["language"] == "it"


def test_pandoc_sandbox_cannot_read_files():
    out = analyze.pandoc_to_latex("![x](/etc/passwd)\n\n```{.include}\n/etc/passwd\n```\n")
    assert "root:" not in out
