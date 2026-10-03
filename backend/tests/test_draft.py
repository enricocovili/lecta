"""Draft typeset by LaTeX block by block: splitting, the per-block cache, renumbering, errors, the pictures."""

from __future__ import annotations

from app.services.blocks import split
from app.services.draft import _crop, _setters, _with_chapter, heading_of


def _spans(src: str) -> list[tuple[int, int]]:
    return [(b.start, b.end) for b in split(src)]


def test_blocks_are_paragraphs_headings_and_whole_environments():
    src = "\n".join([
        "\\chapter{Uno}\\label{ch:uno}",  # 1
        "",
        "\\section{Prima}",  # 3
        "\\label{sec:prima}",  # 4
        "Testo subito sotto.",  # 5
        "Ancora.",  # 6
        "",
        "\\begin{theorem}",  # 8
        "Enunciato.",
        "",  # a blank line inside an environment does not split
        "Seconda parte.",
        "\\end{theorem}",  # 12
        "% solo un commento",  # 13
        "",
        "\\begin{verbatim}",  # 15
        "",
        "  codice \\begin{itemize}",
        "\\end{verbatim}",  # 18
        "",
        "\\lectaimagewithtext{images/a.png}{testo",  # 20
        "",
        "che continua}",  # 22
    ])
    assert _spans(src) == [(1, 1), (3, 4), (5, 6), (8, 12), (15, 18), (20, 22)]
    blocks = split(src)
    assert [b.heading for b in blocks] == [True, True, False, False, False, False]
    assert blocks[3].src.endswith("\\end{theorem}")  # the trailing comment is not part of it


def test_comment_only_text_makes_no_block_and_escaped_percent_is_text():
    assert split("% ==== Lecta: lezione 3\n% altro\n") == []
    assert _spans("Il 50\\% dei casi.\n") == [(1, 1)]


def test_helpers():
    assert heading_of("\\subsection[breve]{Il \\emph{teorema} di Bayes}\\label{x}") == ("subsection", 2, "Il teorema di Bayes")
    state = "equation=0;chapter=4;section=2;"
    assert _with_chapter(state, 1) == "equation=0;chapter=1;section=2;"
    assert _setters("tcb@cnt@x=3;chapter=1;") == "\\lectaset{tcb@cnt@x}{3}\\lectaset{chapter}{1}"
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="595" height="842" viewBox="0 0 595 842"><path d="M0 0"/></svg>'
    cropped, size = _crop(svg, (70.0, 100.0, 300.0, 120.0), 72.0, 450.0)
    assert 'viewBox="68.50 98.50 455.00 23.00"' in cropped and size == {"w": 455.0, "h": 23.0, "x": -3.5}
    assert _crop(svg, (0, 0, 0, 0), 72.0, 450.0) is None  # a blank page


async def _course(admin, chapters=("Primo",)):
    r = await admin.post("/api/courses", json={"name": "Bozza LaTeX", "chapters": list(chapters)})
    assert r.status_code == 201, r.text
    return r.json()


async def _put(admin, cid, path, content):
    r = await admin.put(f"/api/courses/{cid}/files/content", json={"path": path, "content": content})
    assert r.status_code == 200, r.text


CHAPTER = """\\chapter{Primo}

\\section{Gruppi}

Un paragrafo con $x^2$.

\\begin{theorem}\\label{thm:a}
Primo enunciato.
\\end{theorem}

Un altro paragrafo che cita il Teorema~\\ref{thm:a}.

\\begin{theorem}
Secondo enunciato.
\\end{theorem}
"""


async def test_chapter_draft_is_typeset_once_and_then_cached(admin, anon):
    c = await _course(admin)
    cid, ch = c["id"], c["chapters"][0]
    await _put(admin, cid, ch["path"], CHAPTER)
    url = f"/api/courses/{cid}/chapters/{ch['id']}/draft"

    first = (await admin.get(url)).json()
    # Six blocks, then the paragraph citing thm:a once more, now that the label is known (a second pass, as in LaTeX).
    assert first["warnings"] == [] and first["typeset"] == 7
    blocks = first["blocks"]
    assert [(b["start"], b["end"]) for b in blocks] == [(1, 1), (3, 3), (5, 5), (7, 9), (11, 11), (13, 15)]
    assert all(b["pages"] and not b["error"] for b in blocks)
    assert blocks[0]["heading"] == "chapter" and first["toc"] == [{"id": blocks[1]["id"], "title": "Gruppi", "level": 1, "line": 3}]
    pic = blocks[3]["pages"][0]
    assert 300 < pic["w"] < 520 and 5 < pic["h"] < 200 and first["width"] > 300

    svg = await admin.get(pic["url"])
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in svg.headers["content-security-policy"] and svg.text.startswith("<svg")
    assert (await anon.get(pic["url"])).status_code in (401, 403, 404)
    assert (await admin.get(f"/api/courses/{cid}/draft/svg/..%2Findex.json")).status_code == 404

    # Nothing changed: nothing is typeset again.
    again = (await admin.get(url)).json()
    assert again["typeset"] == 0 and [b["pages"] for b in again["blocks"]] == [b["pages"] for b in blocks]

    # A paragraph edited: only that block.
    await _put(admin, cid, ch["path"], CHAPTER.replace("con $x^2$", "con $y^3$"))
    edited = (await admin.get(url)).json()
    assert edited["typeset"] == 1 and edited["blocks"][2]["pages"] != blocks[2]["pages"]
    assert edited["blocks"][3]["pages"] == blocks[3]["pages"]

    # A theorem inserted before the others renumbers them: the theorems are typeset again, the paragraphs are not.
    renum = CHAPTER.replace("Un paragrafo con", "\\begin{theorem}\nNuovo.\n\\end{theorem}\n\nUn paragrafo con")
    await _put(admin, cid, ch["path"], renum)
    moved = (await admin.get(url)).json()
    # The new theorem and the two after it, then (second pass) the paragraph that cites the one whose number changed.
    assert moved["typeset"] == 4
    assert moved["blocks"][3]["pages"] == blocks[2]["pages"]  # the first paragraph: the text it had at first, neutral
    citing = next(b for b in moved["blocks"] if b["start"] == 15)
    assert citing["pages"] and citing["pages"] != blocks[4]["pages"]

    # The whole course in one call.
    whole = (await admin.get(f"/api/courses/{cid}/draft")).json()
    assert [x["chapter"]["id"] for x in whole["chapters"]] == [ch["id"]] and whole["chapters"][0]["typeset"] == 0


async def test_a_block_with_an_error_says_where_and_the_others_are_shown(admin):
    c = await _course(admin)
    cid, ch = c["id"], c["chapters"][0]
    await _put(admin, cid, ch["path"], "\\chapter{Primo}\n\nBuono.\n\nRotto \\comandoinesistente{} qui.\n\nAncora buono.\n")
    res = (await admin.get(f"/api/courses/{cid}/chapters/{ch['id']}/draft")).json()
    blocks = res["blocks"]
    assert len(blocks) == 4 and blocks[1]["error"] is None and blocks[3]["pages"]
    assert blocks[2]["error"] and "riga 5" in blocks[2]["error"] and "Undefined control sequence" in blocks[2]["error"]
    assert blocks[2]["src"].startswith("Rotto")
