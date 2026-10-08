"""The lesson's assistant: asked about a page, it gets the whole lesson and the pictures of the pages around it; it only explains."""

from __future__ import annotations

from PIL import ImageChops

from app.pipeline import lesson_agent
from app.services import lessons as ls

from .test_agent import _events
from .test_ingest_e2e import setup_fake
from .test_lessons import new_lesson, slides_pdf, stroke


async def _lesson(admin, n=25):
    await setup_fake(admin)
    course, lesson = await new_lesson(admin, n=n)
    s = (await admin.post("/api/chat/sessions", json={"course_id": course["id"], "lesson_id": lesson["id"]})).json()
    return course, lesson, s


async def _ask(admin, s, text, **scope):
    r = await admin.post(f"/api/chat/sessions/{s['id']}/messages", json={"content": text, "scope": scope})
    assert r.status_code == 200, r.text
    reply = r.json()["reply"]
    events = await _events(admin, reply["id"])
    return next(d for e, d in events if e in ("done", "error"))["reply"], events


def test_the_pictures_are_the_pages_around_the_asked_one():
    assert lesson_agent.window(60, 30) == list(range(20, 41))
    assert lesson_agent.window(60, 2) == list(range(0, 13))
    assert lesson_agent.window(5, 4) == [0, 1, 2, 3, 4]


def test_pages_are_drawn_with_their_strokes(tmp_path):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(slides_pdf(2))
    pages = [{"kind": "slide", "slide_page": 2, "ratio": 0.7, "ink": []}, {"kind": "slide", "slide_page": 2, "ratio": 0.7, "ink": [stroke()]},
             {"kind": "blank", "slide_page": None, "ratio": 0.75, "ink": [stroke()]}]
    plain, inked, blank = ls.render_pages(pdf, pages, [800, 800, 400])
    assert max(plain.size) == 800 and max(blank.size) == 400
    assert ImageChops.difference(plain, inked).getbbox() is not None
    assert "teorema del campionamento" in ls.slide_texts(pdf, [1])[1]


async def test_the_lesson_has_its_own_conversations(admin):
    course, lesson, s = await _lesson(admin, n=2)
    assert s["lesson_id"] == lesson["id"] and s["lab_id"] is None
    course_session = (await admin.post("/api/chat/sessions", json={"course_id": course["id"]})).json()
    assert [x["id"] for x in (await admin.get("/api/chat/sessions", params={"course_id": course["id"]})).json()] == [course_session["id"]]
    assert [x["id"] for x in (await admin.get("/api/chat/sessions", params={"course_id": course["id"], "lesson_id": lesson["id"]})).json()] == [s["id"]]
    # A lesson of another course, a page of another lesson, a change or a review: refused.
    _, other = await new_lesson(admin, n=1, course_name="Analisi")
    assert (await admin.post("/api/chat/sessions", json={"course_id": course["id"], "lesson_id": other["id"]})).status_code == 404
    r = await admin.post(f"/api/chat/sessions/{s['id']}/messages", json={"content": "x", "scope": {"page_id": other["pages"][0]["id"]}})
    assert r.status_code == 404
    for mode in ("edit", "review"):
        r = await admin.post(f"/api/chat/sessions/{s['id']}/messages", json={"content": "x", "scope": {"mode": mode}})
        assert r.status_code == 422


async def test_a_question_sees_the_pages_around_it_and_the_whole_lesson(admin):
    _, lesson, s = await _lesson(admin)
    page = lesson["pages"][15]
    await admin.put(f"/api/lessons/{lesson['id']}/pages/{page['id']}", json={"notes": "- non capisco perché $f_s > 2B$"})
    reply, events = await _ask(admin, s, "Non capisco questo passaggio", page_id=page["id"])
    assert reply["status"] == "done", reply
    # Pages 6–25 (ten before slide 16, the nine after it in a 25-slide lesson) as pictures, and every page's text and notes.
    assert "Domanda su [p. 16] Slide 16: ho le immagini di 20 pagine ([p. 6] Slide 6 … [p. 25] Slide 25)" in reply["content"]
    assert "la lezione intera (25 pagine, con gli appunti)" in reply["content"]
    assert reply["suggestions"] and reply["change"] is None
    assert not [d for e, d in events if e == "tool"]
    stored = (await admin.get(f"/api/chat/sessions/{s['id']}")).json()["messages"]
    assert stored[0]["scope"] == {"page_id": page["id"], "mode": "ask"}
    # Nothing can be undone: nothing was changed.
    assert (await admin.post(f"/api/chat/replies/{reply['id']}/undo")).status_code == 409


async def test_it_looks_at_another_page_and_reads_the_chapter(admin):
    _, lesson, s = await _lesson(admin, n=3)
    reply, events = await _ask(admin, s, "Cosa c'era prima? FAKE:VIEW 3", page_id=lesson["pages"][0]["id"])
    assert [(d["name"], d["label"]) for e, d in events if e == "tool"] == [("view_page", "Guarda la pagina 3")]
    assert next(d for e, d in events if e == "tool_done")["summary"] == "Slide 3"
    assert "Here is [p. 3] Slide 3" in reply["content"]
    reply, events = await _ask(admin, s, "FAKE:VIEW 9", page_id=lesson["pages"][0]["id"])
    assert next(d for e, d in events if e == "tool_done")["ok"] is False and "pages 1–3" in reply["content"]
    reply, _ = await _ask(admin, s, "Cosa dice il capitolo?")
    assert "not in a chapter yet" in reply["content"]
    # Without a page it still knows the lesson, with no pictures.
    reply, _ = await _ask(admin, s, "Riassumi la lezione")
    assert "nessuna immagine" in reply["content"] and "3 pagine" in reply["content"]


async def test_the_conversations_follow_the_lesson_to_another_course(admin):
    _, lesson, s = await _lesson(admin, n=1)
    other = (await admin.post("/api/courses", json={"name": "Analisi", "language": "it"})).json()
    assert (await admin.patch(f"/api/lessons/{lesson['id']}", json={"course_id": other["id"]})).status_code == 200
    assert [x["id"] for x in (await admin.get("/api/chat/sessions", params={"course_id": other["id"], "lesson_id": lesson["id"]})).json()] == [s["id"]]
