"""The assistant's tools, called directly."""

from __future__ import annotations

import pytest

from app.pipeline import agent_tools as t
from app.pipeline.agent_tools import ToolContext, run_tool

CHAPTER = "\\chapter{Gruppi}\n\\section{Definizione}\nUn gruppo è un insieme.\nUn gruppo è un insieme.\n\\section{Altro}\nFine.\n"


@pytest.fixture
async def course(admin):
    c = (await admin.post("/api/courses", json={"name": "Tools", "language": "it", "chapters": ["Gruppi", "Anelli"]})).json()
    await admin.put(f"/api/courses/{c['id']}/files/content", json={"path": c["chapters"][0]["path"], "content": CHAPTER})
    return c


def ctx(course, mode="edit"):
    return ToolContext(course_id=course["id"], nonce="abc123", mode=mode)


async def test_overview_read_grep(course):
    c = ctx(course)
    ov = await run_tool(c, "course_overview", {})
    assert ov.ok and "«Gruppi»" in ov.text and "Definizione (riga 2)" in ov.text and "«Anelli»" in ov.text
    path = course["chapters"][0]["path"]
    r = await run_tool(c, "read_file", {"path": path, "start_line": 2, "end_line": 3, "line_numbers": True})
    assert "righe 2-3 di 7" in r.text and "    2| \\section{Definizione}" in r.text and "<<<UNTRUSTED-abc123>>>" in r.text
    g = await run_tool(c, "grep", {"pattern": "un GRUPPO"})
    assert f"{path}:3:" in g.text and f"{path}:4:" in g.text
    bad = await run_tool(c, "read_file", {"path": "chapters/nope.tex"})
    assert not bad.ok and "not found" in bad.text
    assert not (await run_tool(c, "read_file", {"path": "images/x.png"})).ok


async def test_edit_file_rules(course):
    c = ctx(course)
    path = course["chapters"][0]["path"]
    r = await run_tool(c, "edit_file", {"path": path, "search": "Un gruppo è un insieme.", "replace": "x"})
    assert not r.ok and "found 2 times" in r.text
    r = await run_tool(c, "edit_file", {"path": path, "search": "Un grupo è un insieme.", "replace": "x"})
    assert not r.ok and "non trovato" not in r.text and "riga 3" in r.text  # hints at the most similar line
    r = await run_tool(c, "edit_file", {"path": path, "search": "Un gruppo è un insieme.", "replace": "Y", "replace_all": True})
    assert r.ok and r.wrote
    r = await run_tool(c, "edit_file", {"path": path, "search": "Fine.", "replace": "\\usepackage{tikz}"})
    assert not r.ok and "only the body" in r.text
    r = await run_tool(c, "edit_file", {"path": path, "search": "Fine.", "replace": "\\immediate\\write18{ls}"})
    assert not r.ok and "forbidden" in r.text
    # Read-only modes have no writing tools at all.
    r = await run_tool(ctx(course, "explain"), "edit_file", {"path": path, "search": "Fine.", "replace": "x"})
    assert not r.ok and "unavailable" in r.text


async def test_write_file_rules(course, admin):
    c = ctx(course)
    r = await run_tool(c, "write_file", {"path": "chapters/99-nuovo.tex", "content": "x"})
    assert not r.ok and "create_chapter" in r.text
    r = await run_tool(c, "write_file", {"path": "../evil.tex", "content": "x"})
    assert not r.ok
    r = await run_tool(c, "write_file", {"path": "figures/schema.tex", "content": "\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}"})
    assert r.ok
    r = await run_tool(c, "write_file", {"path": "preamble.tex", "content": "\\usepackage{amsmath}\n"})
    assert r.ok
    assert (await admin.get(f"/api/courses/{course['id']}")).json()["has_preamble_override"]
    assert (await run_tool(c, "read_file", {"path": "preamble.tex"})).text.count("amsmath") >= 1
    r = await run_tool(c, "write_file", {"path": "main.tex", "content": "no document class"})
    assert not r.ok and "main.tex must keep" in r.text
    # Rewriting a chapter keeps its title in step with the \chapter line.
    path = course["chapters"][0]["path"]
    r = await run_tool(c, "write_file", {"path": path, "content": "\\chapter{Gruppi e sottogruppi}\nTesto.\n"})
    assert r.ok
    assert (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"][0]["title"] == "Gruppi e sottogruppi"


async def test_chapter_tools(course, admin):
    c = ctx(course)
    r = await run_tool(c, "create_chapter", {"title": "Campi", "content": "\\section{Intro}\nTesto.\n", "position": 1})
    assert r.ok and "posizione 1" in r.text
    chapters = (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"]
    assert [x["title"] for x in chapters] == ["Campi", "Gruppi", "Anelli"]
    assert [x["position"] for x in chapters] == [1, 2, 3] and chapters[0]["path"].startswith("chapters/01-")
    cid = chapters[0]["id"]
    assert (await run_tool(c, "move_chapter", {"chapter_id": cid, "position": 3})).ok
    assert (await run_tool(c, "rename_chapter", {"chapter_id": cid, "title": "Campi finiti"})).ok
    chapters = (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"]
    assert [x["title"] for x in chapters] == ["Gruppi", "Anelli", "Campi finiti"]
    main = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": "main.tex"})).json()["content"]
    assert main.index("01-gruppi") < main.index("02-anelli") < main.index("03-campi")
    assert (await run_tool(c, "delete_chapter", {"chapter_id": cid})).ok
    assert not (await run_tool(c, "delete_chapter", {"chapter_id": cid})).ok
    assert not (await run_tool(c, "delete_file", {"path": chapters[0]["path"]})).ok  # structural


async def test_sources_of_other_courses_are_not_readable(course):
    r = await run_tool(ctx(course), "read_source", {"source_file_id": 999999})
    assert not r.ok and "not part of this course" in r.text
    assert (await run_tool(ctx(course), "list_sources", {})).ok
    assert t.specs_for("review") and "write_file" not in {s.name for s in t.specs_for("review")}
    assert "write_file" in {s.name for s in t.specs_for("ask")}


def _slides_with_logo() -> bytes:
    """Three slides with the same logo in the corner (decoration the import drops); the second also has a figure."""
    import io

    import fitz
    from PIL import Image

    from tests.fixtures import block_diagram

    logo = io.BytesIO()
    Image.new("RGB", (120, 120), (200, 30, 30)).save(logo, "PNG")
    fig = io.BytesIO()
    block_diagram().save(fig, "PNG")
    doc = fitz.open()
    for n in range(3):
        p = doc.new_page(width=842, height=595)
        p.insert_text((60, 90), f"Slide {n + 1}", fontsize=32)
        p.insert_image(fitz.Rect(740, 20, 820, 100), stream=logo.getvalue())
        if n == 1:
            p.insert_image(fitz.Rect(120, 170, 720, 420), stream=fig.getvalue())
    return doc.tobytes()


async def _link_slides(course_id: int, chapter_id: int) -> int:
    """The slides of a lesson, as the import leaves them: a source of the course's chapter."""
    from app.db import SessionLocal
    from app.models import SourceFile, SourceLink, Upload
    from app.services import blobs

    data = _slides_with_logo()
    async with SessionLocal() as db:
        up = Upload(status="done", target_course_id=course_id, via="lesson")
        db.add(up)
        await db.flush()
        sf = SourceFile(upload_id=up.id, name="Lezione 3.pdf", kind="pdf", size=len(data), blob=blobs.put_bytes(data), pages=3)
        db.add(sf)
        await db.flush()
        db.add(SourceLink(source_file_id=sf.id, course_id=course_id, chapter_id=chapter_id))
        await db.commit()
        return sf.id


@pytest.fixture
async def slides(course):
    return await _link_slides(course["id"], course["chapters"][0]["id"])


async def test_pictures_are_taken_from_a_slide(course, slides, admin):
    c = ctx(course)
    v = await run_tool(c, "view_source_page", {"source_file_id": slides, "page": 2})
    assert v.ok and v.images
    # The figure and the logo the import leaves out are both listed, in reading order.
    assert "1. immagine [0.88, 0.03, 0.97, 0.17]" in v.text and "2. immagine [0.15, 0.29, 0.85, 0.71]" in v.text
    assert "extract_source_image" in v.text
    assert "extract_source_image" not in (await run_tool(ctx(course, "explain"), "view_source_page", {"source_file_id": slides, "page": 2})).text

    r = await run_tool(c, "extract_source_image", {"source_file_id": slides, "page": 2, "region": 2})
    assert r.ok and r.wrote and r.images and "images/lezione-3-p2.png" in r.text and "\\lectaimage" in r.text
    files = {f["path"] for f in (await admin.get(f"/api/courses/{course['id']}/files")).json()["files"]}
    assert "images/lezione-3-p2.png" in files
    # The embedded picture itself, at its own size, not a render of the page.
    v = await run_tool(c, "view_image", {"path": "images/lezione-3-p2.png"})
    assert v.ok and (v.images[0]["width"], v.images[0]["height"]) == block_diagram_size()

    # Same name again: a new file next to it.
    r = await run_tool(c, "extract_source_image", {"source_file_id": slides, "page": 2, "region": 2})
    assert r.ok and "images/lezione-3-p2-2.png" in r.text
    # A part of the page chosen by the model, with a name of its own; the bbox may come as text.
    r = await run_tool(c, "extract_source_image", {"source_file_id": slides, "page": 1, "bbox": "[0.05, 0.05, 0.5, 0.25]", "name": "Titolo è"})
    assert r.ok and "images/titolo-e.png" in r.text and "il riquadro" in r.text
    r = await run_tool(c, "extract_source_image", {"source_file_id": slides, "page": 3})
    assert r.ok and "la pagina intera" in r.text

    assert "has 2 pictures" in (await run_tool(c, "extract_source_image", {"source_file_id": slides, "page": 2, "region": 5})).text
    assert "has 3 pages" in (await run_tool(c, "extract_source_image", {"source_file_id": slides, "page": 9})).text
    assert not (await run_tool(c, "extract_source_image", {"source_file_id": slides, "page": 1, "bbox": [0.5, 0.5, 0.2, 0.9]})).ok
    assert not (await run_tool(c, "extract_source_image", {"source_file_id": 999999, "page": 1})).ok
    r = await run_tool(ctx(course, "explain"), "extract_source_image", {"source_file_id": slides, "page": 2, "region": 2})
    assert not r.ok and "unavailable" in r.text


def block_diagram_size() -> tuple[int, int]:
    from tests.fixtures import block_diagram

    return block_diagram().size
