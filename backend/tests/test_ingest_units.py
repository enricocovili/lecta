"""Extraction building blocks: Markdown (PDFs: test_pdfextract.py)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.pipeline import analyze

from .fixtures import make_all


@pytest.fixture(scope="module")
def fx():
    d = Path(tempfile.mkdtemp(prefix="lecta-fx-"))
    return make_all(d)


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
