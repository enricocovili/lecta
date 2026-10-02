"""Lessons: pages with typed notes and pen strokes saved as they are written, the annotated downloads, and
the lesson as the input of the import (annotated slides, handwritten pages, notes as the backbone, guidelines)."""

from __future__ import annotations

import fitz
import pytest
from PIL import ImageChops
from sqlalchemy import select

from app.models import Course
from app.pipeline import compose
from app.services import lessons as ls

from .conftest import wait_job
from .test_ingest_e2e import setup_fake


def stroke(t="pen", c="#d32f2f", w=0.006, pts=((0.1, 0.1), (0.5, 0.1), (0.5, 0.3))):
    return {"t": t, "c": c, "w": w, "p": [v for x, y in pts for v in (x, y, 0.5)]}


def slides_pdf(n=3) -> bytes:
    doc = fitz.open()
    for i in range(n):
        p = doc.new_page(width=842, height=595)
        p.insert_text((60, 80), f"Slide {i + 1}: teorema del campionamento", fontsize=28)
        p.insert_text((60, 150), "Un segnale a banda limitata si ricostruisce campionando a più del doppio della banda.", fontsize=16)
    return doc.tobytes()


async def new_lesson(admin, title="Lezione 5", n=3, course_name="Teoria dei Segnali", **course_extra):
    course = (await admin.post("/api/courses", json={"name": course_name, "language": "it", **course_extra})).json()
    lesson = (await admin.post("/api/lessons", json={"course_id": course["id"], "title": title})).json()
    if n:
        r = await admin.post(f"/api/lessons/{lesson['id']}/slides", params={"name": "slide.pdf"}, content=slides_pdf(n),
                             headers={"content-type": "application/octet-stream"})
        assert r.status_code == 200, r.text
    return course, (await admin.get(f"/api/lessons/{lesson['id']}")).json()


# --------------------------------------------------------------------------- units


def test_ink_is_validated():
    ok = ls.clean_ink([stroke(), stroke("hl", "#FFEB3B", 0.03)])
    assert ok[1]["c"] == "#ffeb3b" and len(ok[0]["p"]) == 9
    bad = [
        "x", [{"t": "brush", "c": "#000000", "w": 0.01, "p": [0, 0, 1]}], [stroke(c="red")], [stroke(w=0)], [stroke(w=5)],
        [{"t": "pen", "c": "#000000", "w": 0.01, "p": [0, 0]}], [{"t": "pen", "c": "#000000", "w": 0.01, "p": []}],
        [{"t": "pen", "c": "#000000", "w": 0.01, "p": [0, 0, "a"]}], [{"t": "pen", "c": "#000000", "w": 0.01, "p": [0, 0, float("nan")]}],
        [{"t": "pen", "c": "#000000", "w": 0.01, "p": [99, 0, 1]}], [stroke()] * (ls.MAX_STROKES + 1),
    ]
    for b in bad:
        with pytest.raises(ls.InkError):
            ls.clean_ink(b)


def test_notes_are_one_section_per_slide():
    pages = [
        {"kind": "slide", "slide_page": 1, "notes": "- intro"}, {"kind": "slide", "slide_page": 2, "notes": "  "},
        {"kind": "blank", "slide_page": None, "notes": "un esempio"}, {"kind": "slide", "slide_page": 3, "notes": "- fine"},
    ]
    md = ls.notes_markdown("Lezione 5", pages)
    assert md == "# Lezione 5\n\n## Slide 1\n\n- intro\n\n## Pagina aggiunta dopo la slide 2\n\nun esempio\n\n## Slide 3\n\n- fine\n"
    assert ls.notes_markdown("x", [{"kind": "slide", "slide_page": 1, "notes": ""}]) is None
    assert ls.page_label([{"kind": "blank"}, {"kind": "slide", "slide_page": 1}], 0) == "Pagina aggiunta all'inizio"


def test_deleted_slides_are_left_out_of_the_pdf_and_the_numbering(tmp_path):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(slides_pdf(4))
    pages = [
        {"kind": "slide", "slide_page": 1, "notes": "uno"}, {"kind": "slide", "slide_page": 3, "notes": "tre"},
        {"kind": "blank", "slide_page": None, "notes": "extra"}, {"kind": "slide", "slide_page": 4, "notes": ""},
    ]
    numbered, keep = ls.renumber_slides(pages)
    assert keep == [1, 3, 4] and [p["slide_page"] for p in numbered] == [1, 2, None, 3]
    assert "## Slide 2\n\ntre" in ls.notes_markdown("L", numbered) and "dopo la slide 2" in ls.notes_markdown("L", numbered)
    out = fitz.open(stream=ls.subset_pdf(pdf, keep), filetype="pdf")
    assert out.page_count == 3 and "Slide 3" in out[1].get_text() and "Slide 4" in out[2].get_text()


def test_strokes_are_drawn_on_the_page(tmp_path):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(slides_pdf(1))
    plain = ls.render_slide(pdf, 1, [], side=600)
    inked = ls.render_slide(pdf, 1, [stroke(), stroke("hl", "#ffeb3b", 0.03, ((0.1, 0.2), (0.6, 0.2)))], side=600)
    assert plain.size == inked.size == (600, 424)
    box = ImageChops.difference(plain, inked).getbbox()
    assert box is not None and box[0] < 100 and box[2] > 250
    blank = ls.render_blank(1.4142, [stroke()], side=500)
    assert blank.size == (354, 500) and blank.getextrema() != ((255, 255),) * 3


def test_annotated_pdf_has_every_page_in_order(tmp_path):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(slides_pdf(2))
    pages = [
        {"kind": "slide", "slide_page": 1, "ratio": 0.7, "ink": [stroke()]}, {"kind": "blank", "ratio": 1.4, "ink": [stroke()]},
        {"kind": "slide", "slide_page": 2, "ratio": 0.7, "ink": []},
    ]
    out = fitz.open(stream=ls.annotated_pdf(pdf, pages), filetype="pdf")
    assert out.page_count == 3
    assert "Slide 1" in out[0].get_text() and out[1].get_text().strip() == "" and "Slide 2" in out[2].get_text()
    assert out[1].rect.height > out[1].rect.width and len(out[0].get_drawings()) >= 1


# --------------------------------------------------------------------------- the API


async def test_lesson_pages_are_saved_and_reloaded(admin):
    course, lesson = await new_lesson(admin)
    assert lesson["has_pdf"] and lesson["pdf_pages"] == 3 and [p["kind"] for p in lesson["pages"]] == ["slide"] * 3
    assert [p["slide_page"] for p in lesson["pages"]] == [1, 2, 3] and lesson["pages"][0]["ratio"] == pytest.approx(0.7071, abs=1e-3)
    lid, p1, p2 = lesson["id"], lesson["pages"][0], lesson["pages"][1]

    r = await admin.put(f"/api/lessons/{lid}/pages/{p1['id']}", json={"notes": "- campionamento\n- Nyquist $f_s > 2B$", "ink": [stroke()]})
    assert r.status_code == 200 and r.json()["version"] == 2
    # Saving only the notes leaves the strokes alone.
    await admin.put(f"/api/lessons/{lid}/pages/{p1['id']}", json={"notes": "- campionamento\n- Nyquist $f_s > 2B$ (rivisto)"})
    got = (await admin.get(f"/api/lessons/{lid}")).json()["pages"][0]
    assert got["notes"].endswith("(rivisto)") and len(got["ink"]) == 1 and got["ink"][0]["c"] == "#d32f2f"

    assert (await admin.put(f"/api/lessons/{lid}/pages/{p2['id']}", json={"ink": [stroke(c="rosso")]})).status_code == 422
    assert (await admin.put(f"/api/lessons/{lid}/pages/999999", json={"notes": "x"})).status_code == 404

    # A blank page after the second slide (the slide had no room left), then remove it again.
    added = (await admin.post(f"/api/lessons/{lid}/pages", json={"after_page_id": p2["id"]})).json()
    assert added["kind"] == "blank" and added["position"] == 3 and added["ratio"] == pytest.approx(0.7071, abs=1e-3)
    order = [(p["kind"], p["position"]) for p in (await admin.get(f"/api/lessons/{lid}")).json()["pages"]]
    assert order == [("slide", 1), ("slide", 2), ("blank", 3), ("slide", 4)]
    assert (await admin.delete(f"/api/lessons/{lid}/pages/{added['id']}")).status_code == 200
    assert [p["position"] for p in (await admin.get(f"/api/lessons/{lid}")).json()["pages"]] == [1, 2, 3]

    listed = (await admin.get("/api/lessons", params={"course_id": course["id"]})).json()
    assert len(listed) == 1 and listed[0]["page_count"] == 3 and listed[0]["notes_pages"] == 1 and listed[0]["ink_pages"] == 1
    assert listed[0]["course_name"] == "Teoria dei Segnali"

    # A slide can be deleted too (the PDF stays as it is, the page just leaves the lesson); the last page cannot.
    assert (await admin.delete(f"/api/lessons/{lid}/pages/{p2['id']}")).status_code == 200
    left = (await admin.get(f"/api/lessons/{lid}")).json()["pages"]
    assert [(p["position"], p["slide_page"]) for p in left] == [(1, 1), (2, 3)]
    assert (await admin.delete(f"/api/lessons/{lid}/pages/{left[0]['id']}")).status_code == 200
    assert (await admin.delete(f"/api/lessons/{lid}/pages/{left[1]['id']}")).status_code == 409


async def test_a_removed_page_comes_back_with_its_id_notes_and_strokes(admin):
    _, lesson = await new_lesson(admin)
    lid = lesson["id"]
    p1, p2, p3 = lesson["pages"]
    await admin.put(f"/api/lessons/{lid}/pages/{p2['id']}", json={"notes": "dalla slide due", "ink": [stroke()]})
    before = (await admin.get(f"/api/lessons/{lid}")).json()["pages"][1]
    assert (await admin.delete(f"/api/lessons/{lid}/pages/{p2['id']}")).status_code == 200
    body = {k: before[k] for k in ("id", "kind", "slide_page", "ratio", "position", "notes", "ink")}

    r = await admin.post(f"/api/lessons/{lid}/pages/restore", json=body)
    assert r.status_code == 201 and r.json()["id"] == p2["id"]
    got = (await admin.get(f"/api/lessons/{lid}")).json()["pages"]
    assert [(p["id"], p["position"], p["slide_page"]) for p in got] == [(p1["id"], 1, 1), (p2["id"], 2, 2), (p3["id"], 3, 3)]
    assert got[1]["notes"] == "dalla slide due" and got[1]["ink"][0]["c"] == "#d32f2f"

    # It cannot come back twice, nor as a page that never existed or a slide the PDF does not have.
    assert (await admin.post(f"/api/lessons/{lid}/pages/restore", json=body)).status_code == 409
    assert (await admin.post(f"/api/lessons/{lid}/pages/restore", json={**body, "id": 10**9})).status_code == 422
    await admin.delete(f"/api/lessons/{lid}/pages/{p3['id']}")
    assert (await admin.post(f"/api/lessons/{lid}/pages/restore", json={**body, "id": p3["id"], "slide_page": 9, "position": 3})).status_code == 422
    # A blank page comes back too, and a position past the end puts it last.
    blank = (await admin.post(f"/api/lessons/{lid}/pages", json={})).json()
    await admin.delete(f"/api/lessons/{lid}/pages/{blank['id']}")
    back = await admin.post(f"/api/lessons/{lid}/pages/restore", json={"id": blank["id"], "kind": "blank", "ratio": blank["ratio"], "position": 99, "notes": "x"})
    assert back.status_code == 201 and back.json()["position"] == 3 and back.json()["slide_page"] is None


async def test_share_links_open_the_lesson_without_signing_in(admin, anon):
    course, lesson = await new_lesson(admin)
    lid, p1 = lesson["id"], lesson["pages"][0]
    await admin.put(f"/api/lessons/{lid}/pages/{p1['id']}", json={"notes": "- dal proprietario", "ink": [stroke()]})
    assert (await admin.get(f"/api/lessons/{lid}/shares")).json() == []

    read = (await admin.post(f"/api/lessons/{lid}/shares", json={"mode": "read"})).json()
    write = (await admin.post(f"/api/lessons/{lid}/shares", json={"mode": "write"})).json()
    assert read["token"] != write["token"] and len(read["token"]) >= 30
    # Asking again gives the same link; «new_link» replaces it and the old one stops working.
    assert (await admin.post(f"/api/lessons/{lid}/shares", json={"mode": "read"})).json()["token"] == read["token"]
    assert [s["mode"] for s in (await admin.get(f"/api/lessons/{lid}/shares")).json()] == ["read", "write"]

    # Nobody signed in sees the lesson with the link: slides, notes and strokes, nothing of the owner's side.
    r = await anon.get(f"/api/public/lesson/{read['token']}")
    got = r.json()
    assert r.status_code == 200 and got["mode"] == "read" and got["title"] == "Lezione 5" and got["pages"][0]["notes"] == "- dal proprietario"
    assert len(got["pages"][0]["ink"]) == 1 and "course_guidelines" not in got and "chapter_id" not in got and "id" not in got
    assert (await anon.get(f"/api/public/lesson/{read['token']}/pdf")).headers["content-type"] == "application/pdf"
    assert (await anon.get(f"/api/public/lesson/{read['token']}/notes.md")).status_code == 200
    assert (await anon.get(f"/api/public/lesson/{read['token']}/annotated.pdf")).content[:5] == b"%PDF-"
    assert (await anon.get("/api/public/lesson/not-a-token")).status_code == 404

    # A read link does not write; a write link writes notes and strokes, adds, removes and restores pages.
    save = {"notes": "- da un amico"}
    assert (await anon.put(f"/api/public/lesson/{read['token']}/pages/{p1['id']}", json=save)).status_code == 403
    assert (await anon.post(f"/api/public/lesson/{read['token']}/pages", json={})).status_code == 403
    assert (await anon.put(f"/api/public/lesson/{write['token']}/pages/{p1['id']}", json=save)).status_code == 200
    assert (await admin.get(f"/api/lessons/{lid}")).json()["pages"][0]["notes"] == "- da un amico"
    added = await anon.post(f"/api/public/lesson/{write['token']}/pages", json={"after_page_id": p1["id"]})
    assert added.status_code == 201
    assert (await anon.delete(f"/api/public/lesson/{write['token']}/pages/{added.json()['id']}")).status_code == 200
    body = {k: added.json()[k] for k in ("id", "kind", "slide_page", "ratio", "position", "notes", "ink")}
    assert (await anon.post(f"/api/public/lesson/{write['token']}/pages/restore", json=body)).status_code == 201
    # A token only opens its own lesson.
    _, second = await new_lesson(admin, title="Altra", course_name="Altra materia")
    assert (await anon.put(f"/api/public/lesson/{write['token']}/pages/{second['pages'][0]['id']}", json=save)).status_code == 404

    # Following what others write: the order, and only the pages whose version differs.
    first = (await anon.post(f"/api/public/lesson/{read['token']}/sync", json={"known": {}})).json()
    assert len(first["pages"]) == 4 and first["order"][:2] == [p1["id"], added.json()["id"]]
    known = {str(p["id"]): p["version"] for p in first["pages"]}
    assert (await anon.post(f"/api/public/lesson/{read['token']}/sync", json={"known": known})).json()["pages"] == []
    await admin.put(f"/api/lessons/{lid}/pages/{p1['id']}", json={"notes": "cambiato"})
    changed = (await anon.post(f"/api/public/lesson/{read['token']}/sync", json={"known": known})).json()
    assert [p["id"] for p in changed["pages"]] == [p1["id"]] and changed["pages"][0]["notes"] == "cambiato"

    # Revoking a link, replacing it, deleting the lesson: the link stops working.
    assert (await admin.delete(f"/api/lessons/{lid}/shares/read")).status_code == 200
    assert (await anon.get(f"/api/public/lesson/{read['token']}")).status_code == 404
    new = (await admin.post(f"/api/lessons/{lid}/shares", json={"mode": "write", "new_link": True})).json()
    assert new["token"] != write["token"] and (await anon.get(f"/api/public/lesson/{write['token']}")).status_code == 404
    assert (await anon.get(f"/api/public/lesson/{new['token']}")).status_code == 200
    await admin.delete(f"/api/lessons/{lid}")
    assert (await anon.get(f"/api/public/lesson/{new['token']}")).status_code == 404
    # Managing the links is for the owner.
    assert (await anon.post(f"/api/lessons/{lid}/shares", json={"mode": "read"})).status_code == 404


async def test_lessons_are_numbered_within_their_course(admin):
    course, first = await new_lesson(admin, title="Prima", n=0)
    assert first["number"] == 1
    second = (await admin.post("/api/lessons", json={"course_id": course["id"], "title": "Seconda"})).json()
    other, theirs = await new_lesson(admin, title="Altro corso", n=0, course_name="Fisica")
    assert second["number"] == 2 and theirs["number"] == 1  # numbers restart in every course
    got = await admin.get(f"/api/courses/{course['id']}/lessons/2")
    assert got.status_code == 200 and got.json()["id"] == second["id"] and got.json()["title"] == "Seconda"
    assert (await admin.get(f"/api/courses/{other['id']}/lessons/2")).status_code == 404  # the number is of that course
    # Deleting a lesson does not renumber the others (their addresses stay), and the next one takes a new number.
    await admin.delete(f"/api/lessons/{first['id']}")
    assert (await admin.get(f"/api/courses/{course['id']}/lessons/1")).status_code == 404
    third = (await admin.post("/api/lessons", json={"course_id": course["id"], "title": "Terza"})).json()
    assert third["number"] == 3 and (await admin.get(f"/api/courses/{course['id']}/lessons/2")).json()["id"] == second["id"]
    # Moving a lesson to another course gives it the next number there.
    moved = (await admin.patch(f"/api/lessons/{third['id']}", json={"course_id": other["id"]})).json()
    assert moved["number"] == 2


async def test_the_last_open_page_is_remembered(admin):
    _, lesson = await new_lesson(admin, n=3)
    lid, pages = lesson["id"], lesson["pages"]
    assert lesson["last_page_id"] is None
    before = (await admin.get(f"/api/lessons/{lid}")).json()["updated_at"]
    assert (await admin.put(f"/api/lessons/{lid}/last-page", json={"page_id": pages[2]["id"]})).status_code == 200
    got = (await admin.get(f"/api/lessons/{lid}")).json()
    assert got["last_page_id"] == pages[2]["id"] and got["updated_at"] == before  # looking is not editing
    # A page of another lesson is refused; deleting the page forgets it.
    _, other = await new_lesson(admin, title="Altra", n=1)
    assert (await admin.put(f"/api/lessons/{lid}/last-page", json={"page_id": other["pages"][0]["id"]})).status_code == 404
    assert (await admin.delete(f"/api/lessons/{lid}/pages/{pages[2]['id']}")).status_code == 200
    assert (await admin.get(f"/api/lessons/{lid}")).json()["last_page_id"] is None


async def test_lesson_downloads(admin):
    _, lesson = await new_lesson(admin, title="Campionamento")
    lid, p1 = lesson["id"], lesson["pages"][0]
    await admin.put(f"/api/lessons/{lid}/pages/{p1['id']}", json={"notes": "- Nyquist", "ink": [stroke()]})
    pdf = await admin.get(f"/api/lessons/{lid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    ann = await admin.get(f"/api/lessons/{lid}/annotated.pdf")
    assert ann.status_code == 200 and fitz.open(stream=ann.content, filetype="pdf").page_count == 3
    md = await admin.get(f"/api/lessons/{lid}/notes.md")
    assert md.text == "# Campionamento\n\n## Slide 1\n\n- Nyquist\n"


async def test_a_lesson_without_slides_and_bad_slides(admin):
    _, lesson = await new_lesson(admin, n=0)
    lid = lesson["id"]
    assert not lesson["has_pdf"] and len(lesson["pages"]) == 1 and lesson["pages"][0]["ratio"] == pytest.approx(1.4142)
    r = await admin.post(f"/api/lessons/{lid}/slides", content=b"not a pdf", headers={"content-type": "application/octet-stream"})
    assert r.status_code == 400
    # Slides replace the blank page while it is untouched, but not one already written on.
    await admin.put(f"/api/lessons/{lid}/pages/{lesson['pages'][0]['id']}", json={"notes": "scritto prima delle slide"})
    r = await admin.post(f"/api/lessons/{lid}/slides", content=slides_pdf(2), headers={"content-type": "application/octet-stream"})
    assert r.status_code == 200
    kinds = [(p["kind"], p["notes"]) for p in r.json()["pages"]]
    assert kinds == [("slide", ""), ("slide", ""), ("blank", "scritto prima delle slide")]
    again = await admin.post(f"/api/lessons/{lid}/slides", content=slides_pdf(1), headers={"content-type": "application/octet-stream"})
    assert again.status_code == 409


async def test_lessons_belong_to_the_admin(admin, anon):
    _, lesson = await new_lesson(admin)
    assert (await anon.get(f"/api/lessons/{lesson['id']}")).status_code == 404
    assert (await anon.put(f"/api/lessons/{lesson['id']}/pages/{lesson['pages'][0]['id']}", json={"notes": "x"})).status_code == 404


async def test_deleting_the_course_deletes_its_lessons(admin):
    course, lesson = await new_lesson(admin, course_name="Da eliminare")
    r = await admin.post(f"/api/courses/{course['id']}/delete", json={"confirm_slug": course["slug"]})
    assert r.status_code == 200
    assert (await admin.get(f"/api/lessons/{lesson['id']}")).status_code == 404


# --------------------------------------------------------------------------- generating the text


async def test_empty_lesson_cannot_be_generated(admin):
    _, lesson = await new_lesson(admin, n=0)
    r = await admin.post(f"/api/lessons/{lesson['id']}/generate", json={})
    assert r.status_code == 400


async def test_generating_a_lesson_writes_the_course_text(admin, worker, monkeypatch):
    await setup_fake(admin)
    seen: list[str] = []
    real_ai = compose.ai

    async def spy(ctx, req, **kw):  # noqa: ANN001
        if req.task == "notes.compose":
            seen.append("\n".join(p.text or "" for m in req.messages for p in m.parts if p.type == "text"))
        return await real_ai(ctx, req, **kw)

    monkeypatch.setattr(compose, "ai", spy)

    course, lesson = await new_lesson(admin, title="Campionamento", n=3)
    lid, pages = lesson["id"], lesson["pages"]
    await admin.put(f"/api/lessons/{lid}/pages/{pages[0]['id']}", json={"notes": "- la frequenza di campionamento deve superare 2B"})
    await admin.put(f"/api/lessons/{lid}/pages/{pages[1]['id']}", json={"ink": [stroke(), stroke("hl", "#ffeb3b", 0.03, ((0.1, 0.4), (0.7, 0.4)))]})
    extra = (await admin.post(f"/api/lessons/{lid}/pages", json={"after_page_id": pages[2]["id"]})).json()
    await admin.put(f"/api/lessons/{lid}/pages/{extra['id']}", json={"notes": "esempio: musica a 44.1 kHz", "ink": [stroke(pts=((0.2, 0.2), (0.8, 0.5)))]})

    guide = "Scrivi in modo discorsivo, con un esempio per ogni definizione."
    r = await admin.post(f"/api/lessons/{lid}/generate", json={"guidelines": guide})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    # A second press while it runs is refused.
    assert (await admin.post(f"/api/lessons/{lid}/generate", json={})).status_code == 409
    j = await wait_job(admin, job_id, timeout=300)
    assert j["status"] == "succeeded", (j["error"], [x["message"] for x in j.get("logs", [])[-15:]])

    manifest = (await admin.get(f"/api/jobs/{job_id}/ingest")).json()
    by_kind = {}
    for it in manifest["items"]:
        by_kind.setdefault(it["kind"], []).append(it)
    # The slide with strokes goes to the reading step as a picture, the page written by hand as handwriting.
    annotated = [i for i in manifest["items"] if i["page"] == 2]
    assert annotated and annotated[0]["kind"] == "page_image" and "handwriting of the student" in annotated[0]["meta"]["why"]
    assert len(by_kind["handwritten"]) == 1 and len(by_kind["markdown"]) == 1
    assert by_kind["markdown"][0]["title"] == "Campionamento"
    assert "Slide 1" in by_kind["markdown"][0]["text"] and "Pagina aggiunta dopo la slide 3" in by_kind["markdown"][0]["text"]
    # One lesson → one group → one chapter in the lesson's course.
    assert len(manifest["results"]) == 1 and manifest["results"][0]["type"] == "new_chapter"
    assert manifest["results"][0]["course_id"] == course["id"]
    assert len(seen) == 1 and guide in seen[0] and "Slide 1" in seen[0]

    detail = (await admin.get(f"/api/courses/{course['id']}")).json()
    assert detail["guidelines"] == guide and len(detail["chapters"]) == 1
    text = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": detail["chapters"][0]["path"]})).json()["content"]
    assert "2B" in text

    done = (await admin.get(f"/api/lessons/{lid}")).json()
    assert done["generated_at"] and done["last_result"]["job_id"] == job_id
    assert done["last_result"]["chapters"][0]["chapter_id"] == detail["chapters"][0]["id"]
    assert done["course_guidelines"] == guide

    # Running it again without guidelines in the request keeps the saved ones; with an empty text and «save» it clears them.
    r = await admin.post(f"/api/lessons/{lid}/generate", json={"chapter_id": detail["chapters"][0]["id"]})
    j2 = await wait_job(admin, r.json()["job_id"], timeout=300)
    assert j2["status"] == "succeeded" and j2["result"]["groups"][0]["type"] == "append"
    assert len(seen) == 2 and guide in seen[1]
    r = await admin.post(f"/api/lessons/{lid}/generate", json={"guidelines": "", "chapter_id": detail["chapters"][0]["id"]})
    j3 = await wait_job(admin, r.json()["job_id"], timeout=300)
    assert j3["status"] == "succeeded" and len(seen) == 3 and guide not in seen[2] and "guidelines for writing this subject" not in seen[2]
    assert any("nessuna linea guida" in x["message"] for x in j3["logs"])
    assert (await admin.get(f"/api/courses/{course['id']}")).json()["guidelines"] == ""

    # The lesson lives in its chapter now: generating again updates that chapter (no second chapter), its section is
    # rewritten in place and the previous version goes to the writing step.
    assert done["chapter"]["id"] == detail["chapters"][0]["id"]
    r = await admin.post(f"/api/lessons/{lid}/generate", json={})
    j4 = await wait_job(admin, r.json()["job_id"], timeout=300)
    assert j4["status"] == "succeeded" and j4["result"]["groups"][0]["type"] == "append" and j4["result"]["groups"][0]["replaced"]
    assert len((await admin.get(f"/api/courses/{course['id']}")).json()["chapters"]) == 1
    assert "PREVIOUS VERSION" in seen[3] and "2B" in seen[3]

    # A chapter of its own is still one request away.
    r = await admin.post(f"/api/lessons/{lid}/generate", json={"placement": "new_chapter"})
    j5 = await wait_job(admin, r.json()["job_id"], timeout=300)
    assert j5["status"] == "succeeded" and j5["result"]["groups"][0]["type"] == "new_chapter"
    assert len((await admin.get(f"/api/courses/{course['id']}")).json()["chapters"]) == 2
    assert (await admin.get(f"/api/lessons/{lid}")).json()["chapter"]["id"] == j5["result"]["groups"][0]["chapter_id"]


async def test_editing_a_lesson_updates_its_section_of_the_chapter(admin, worker):
    """A lesson already in the course text is updated in place: the rest of the chapter stays and the section isn't duplicated."""
    await setup_fake(admin)
    course, lesson = await new_lesson(admin, title="Filtri", n=1)
    lid, page = lesson["id"], lesson["pages"][0]
    assert (await admin.post(f"/api/lessons/{lid}/generate", json={"placement": "update"})).status_code in (400, 409)
    await admin.put(f"/api/lessons/{lid}/pages/{page['id']}", json={"notes": "- il filtro passa basso lascia passare le basse frequenze"})
    j = await wait_job(admin, (await admin.post(f"/api/lessons/{lid}/generate", json={})).json()["job_id"], timeout=300)
    assert j["status"] == "succeeded", j["error"]
    ch = (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"][0]

    async def text() -> str:
        return (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]

    first = await text()
    assert first.count(f"% ==== Lecta: lezione {lid} ") == 1 and first.count(f"% ==== Lecta: fine lezione {lid}") == 1
    assert "passa basso" in first
    # The student writes in the chapter outside the lesson's section.
    mine = first.rstrip() + "\n\n\\section{Una mia aggiunta}\nQuesto lo ho scritto io.\n"
    assert (await admin.put(f"/api/courses/{course['id']}/files/content", json={"path": ch["path"], "content": mine})).status_code == 200

    await admin.put(f"/api/lessons/{lid}/pages/{page['id']}", json={"notes": "- il filtro passa basso lascia passare le basse frequenze\n- il passa alto taglia le basse"})
    j = await wait_job(admin, (await admin.post(f"/api/lessons/{lid}/generate", json={})).json()["job_id"], timeout=300)
    assert j["status"] == "succeeded", j["error"]
    assert len((await admin.get(f"/api/courses/{course['id']}")).json()["chapters"]) == 1
    second = await text()
    assert second.count(f"% ==== Lecta: lezione {lid} ") == 1 and second.count("passa basso lascia") == 1
    assert "passa alto" in second and "Questo lo ho scritto io." in second


async def test_guidelines_can_be_used_once_without_saving(admin, worker, db):
    await setup_fake(admin)
    course, lesson = await new_lesson(admin, n=1)
    await admin.put(f"/api/lessons/{lesson['id']}/pages/{lesson['pages'][0]['id']}", json={"notes": "- un punto"})
    await admin.patch(f"/api/courses/{course['id']}", json={"guidelines": "  Testo breve.  "})
    r = await admin.post(f"/api/lessons/{lesson['id']}/generate", json={"guidelines": "Solo per questa volta.", "save_guidelines": False})
    j = await wait_job(admin, r.json()["job_id"], timeout=300)
    assert j["status"] == "succeeded" and any("linee guida della materia" in x["message"] for x in j["logs"])
    row = (await db.execute(select(Course).where(Course.id == course["id"]))).scalar_one()
    await db.refresh(row)
    assert row.guidelines == "Testo breve."


async def test_upload_into_a_subject_carries_the_guidelines(admin, worker, monkeypatch):
    """The upload page asks for the subject's guidelines too: they reach the writing step and can be kept for the course."""
    from .test_ingest_e2e import upload  # noqa: F401  (same helpers, but the finish call is made here to pass the guidelines)

    await setup_fake(admin)
    seen: list[str] = []
    real_ai = compose.ai

    async def spy(ctx, req, **kw):  # noqa: ANN001
        if req.task == "notes.compose":
            seen.append("\n".join(p.text or "" for m in req.messages for p in m.parts if p.type == "text"))
        return await real_ai(ctx, req, **kw)

    monkeypatch.setattr(compose, "ai", spy)
    course = (await admin.post("/api/courses", json={"name": "Fisica 1", "language": "it"})).json()

    async def run(**finish) -> dict:
        up = (await admin.post("/api/uploads", json={"course_id": course["id"]})).json()["id"]
        r = await admin.post(f"/api/uploads/{up}/files", params={"name": "note.md"}, content=b"# Cinematica\n\n- velocita media\n",
                             headers={"content-type": "application/octet-stream"})
        assert r.status_code == 200, r.text
        fin = await admin.post(f"/api/uploads/{up}/finish", json=finish)
        assert fin.status_code == 200, fin.text
        j = await wait_job(admin, fin.json()["job_id"], timeout=300)
        assert j["status"] == "succeeded", j["error"]
        return j

    await run(guidelines="Punti elenco brevi.", save_guidelines=True)
    assert "Punti elenco brevi." in seen[0]
    assert (await admin.get(f"/api/courses/{course['id']}")).json()["guidelines"] == "Punti elenco brevi."
    # Nothing said: the course's own are used. Used once without keeping: the saved ones stay.
    await run()
    assert "Punti elenco brevi." in seen[1]
    await run(guidelines="Solo questa volta.")
    assert "Solo questa volta." in seen[2] and "Punti elenco brevi." not in seen[2]
    assert (await admin.get(f"/api/courses/{course['id']}")).json()["guidelines"] == "Punti elenco brevi."
    # An empty text means automatic.
    await run(guidelines="")
    assert "guidelines for writing this subject" not in seen[3]
