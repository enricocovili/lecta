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
