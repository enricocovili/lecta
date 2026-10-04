"""End to end: a lesson through the whole import pipeline with the fake provider, written straight into the course
(no approvals, no review). The lesson's notes are the backbone of the text, the slides complete them."""

from __future__ import annotations

import fitz
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import AuditLog

from .conftest import wait_job


async def setup_fake(admin) -> int:
    r = await admin.post("/api/providers", json={"name": "Fake", "type": "fake"})
    pid = r.json()["id"]
    roles = {role: {"primary": {"provider_id": pid, "model": "fake-large"}} for role in
             ("vision", "handwriting", "writing", "classification", "chat")}
    r = await admin.put("/api/settings/roles", json=roles)
    assert r.status_code == 200, r.text
    return pid


async def lesson(admin, course_id: int, slides: bytes | None = None, notes: list[str] = (), ink: dict[int, list] | None = None,
                 extra_page: tuple[str, list] | None = None, chapter_id: int | None = None, title: str = "Lezione") -> int:
    """Write a lesson (slides, notes on its pages in order, strokes, a page added at the end) and add it to the text: the job id."""
    lid = (await admin.post("/api/lessons", json={"course_id": course_id, "title": title})).json()["id"]
    if slides:
        r = await admin.post(f"/api/lessons/{lid}/slides", params={"name": "slide.pdf"}, content=slides,
                             headers={"content-type": "application/octet-stream"})
        assert r.status_code == 200, r.text
    pages = (await admin.get(f"/api/lessons/{lid}")).json()["pages"]
    for page, text in zip(pages, notes):
        await admin.put(f"/api/lessons/{lid}/pages/{page['id']}", json={"notes": text})
    for i, strokes in (ink or {}).items():
        await admin.put(f"/api/lessons/{lid}/pages/{pages[i]['id']}", json={"ink": strokes})
    if extra_page:
        extra = (await admin.post(f"/api/lessons/{lid}/pages", json={"after_page_id": pages[-1]["id"]})).json()
        await admin.put(f"/api/lessons/{lid}/pages/{extra['id']}", json={"notes": extra_page[0], "ink": extra_page[1]})
    r = await admin.post(f"/api/lessons/{lid}/generate", json={"chapter_id": chapter_id} if chapter_id else {})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def stroke(pts=((0.2, 0.2), (0.8, 0.5))) -> dict:
    return {"t": "pen", "c": "#1a1a1a", "w": 0.006, "p": [v for x, y in pts for v in (x, y, 0.5)]}


async def _done(admin, job_id: int, timeout: float = 300) -> dict:
    j = await wait_job(admin, job_id, timeout=timeout)
    assert j["status"] == "succeeded", (j["error"], [x["message"] for x in j.get("logs", [])[-15:]])
    return j


async def _tasks(job_id: int) -> list[str]:
    async with SessionLocal() as db:
        return list((await db.execute(select(AuditLog.task).where(AuditLog.job_id == job_id).order_by(AuditLog.id))).scalars())


def _slides(pages: list[str]) -> bytes:
    doc = fitz.open()
    for i, text in enumerate(pages):
        p = doc.new_page(width=842, height=595)
        p.insert_text((60, 80), f"Lezione {i + 1}", fontsize=32)
        p.insert_text((60, 150), text, fontsize=18)
    return doc.tobytes()


async def test_a_lesson_into_a_new_course_writes_a_compiling_chapter(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Teoria dei Segnali", "language": "it"})).json()
    notes = ["- un sistema LTI è lineare e tempo-invariante\n- convoluzione: $y(t) = \\int_{-\\infty}^{+\\infty} x(\\tau) h(t-\\tau)\\,d\\tau$",
             "```\nx --> [h] --> y\n```"]
    job_id = await lesson(admin, course["id"], _slides(["Sistemi LTI", "Risposta all'impulso", "Convoluzione"]), notes,
                          ink={1: [stroke()]}, extra_page=("esempio a mano", [stroke(((0.1, 0.1), (0.9, 0.9)))]), title="Sistemi LTI")
    await _done(admin, job_id)

    manifest = (await admin.get(f"/api/jobs/{job_id}/ingest")).json()
    kinds = sorted(i["kind"] for i in manifest["items"])
    assert kinds == ["handwritten", "markdown", "page", "page", "page_image"], kinds
    # One lesson → one group → one new chapter, written directly.
    assert len(manifest["results"]) == 1 and manifest["results"][0]["type"] == "new_chapter"
    # Reading, then one text written from the notes (no diagrams, no placement: the course was empty).
    tasks = await _tasks(job_id)
    assert set(tasks) == {"read.pages", "read.handwriting", "notes.compose"} and tasks.count("notes.compose") == 1

    detail = (await admin.get(f"/api/courses/{course['id']}")).json()
    assert len(detail["chapters"]) == 1
    ch = detail["chapters"][0]
    text = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    assert "\\chapter{" in text and "\\lectafigure" not in text and "tikzpicture" not in text
    assert "\\int_{-\\infty}^{+\\infty}" in text  # the Markdown math survived
    assert "\\begin{verbatim}" in text  # ASCII diagrams are kept as code
    sources = (await admin.get(f"/api/courses/{course['id']}/chapters/{ch['id']}/sources")).json()
    assert {s["name"] for s in sources} >= {"slide.pdf", "sistemi-lti-appunti.md"}
    res = (await admin.post(f"/api/courses/{course['id']}/compile", json={"full": True})).json()
    assert res["status"] == "ok", [d for d in res["diagnostics"] if d["level"] == "error"][:5]
    pdf = await admin.get(f"/api/courses/{course['id']}/pdf")
    pdf_text = "".join(pg.get_text() for pg in fitz.open(stream=pdf.content, filetype="pdf"))
    assert "Sistemi" in pdf_text


async def test_a_lesson_into_a_chapter_appends(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Segnali e Sistemi", "language": "it", "chapters": ["Sistemi LTI"]})).json()
    ch = course["chapters"][0]
    await admin.put(
        f"/api/courses/{course['id']}/files/content",
        json={"path": ch["path"], "content": "\\chapter{Sistemi LTI}\n\\section{Introduzione}\nUn sistema LTI è lineare e tempo-invariante.\n"},
    )
    job_id = await lesson(admin, course["id"], notes=["# Sovrapposizione\n\n- vale il principio di sovrapposizione"], chapter_id=ch["id"])
    j = await _done(admin, job_id)
    assert j["result"]["groups"][0]["type"] == "append"
    content = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    assert content.index("Un sistema LTI è lineare") < content.index("principio di sovrapposizione")
    assert await _tasks(job_id) == ["notes.compose"]  # notes alone: nothing to read, one text to write
    detail = (await admin.get(f"/api/courses/{course['id']}")).json()
    assert len(detail["chapters"]) == 1


async def test_bad_latex_gets_one_automatic_fix(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Correzioni", "language": "it"})).json()
    job_id = await lesson(admin, course["id"], _slides(["Testo con un errore FAKE:BADLATEX"]))
    j = await _done(admin, job_id)
    assert (await _tasks(job_id)).count("notes.fix") == 1
    assert j["result"]["groups"][0]["compile"] == "ok"
    ch = (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"][0]
    text = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    assert "\\undefinedmacro" not in text


async def test_truncated_reply_splits_the_unit(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Tagli", "language": "it"})).json()
    job_id = await lesson(admin, course["id"], _slides(["Primo FAKE:TRUNCATE", "Secondo", "Terzo", "Quarto"]))
    j = await _done(admin, job_id)
    assert any("two halves" in x["message"] for x in j["logs"])
    ch = (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"][0]
    text = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    assert "Secondo" in text and "Quarto" in text


async def test_retries_dont_write_twice_or_resend(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Ripetizioni", "language": "it"})).json()
    job_id = await lesson(admin, course["id"], _slides(["Unico contenuto"]))
    await _done(admin, job_id)

    async def calls() -> int:
        async with SessionLocal() as db:
            return (await db.execute(select(func.count()).select_from(AuditLog).where(AuditLog.job_id == job_id))).scalar_one()

    before = await calls()
    assert (await admin.post(f"/api/jobs/{job_id}/retry", json={})).status_code == 200
    await _done(admin, job_id)
    assert await calls() == before  # memoised: nothing is sent again
    assert (await admin.post(f"/api/jobs/{job_id}/retry", json={"from_scratch": True})).status_code == 200
    await _done(admin, job_id)
    detail = (await admin.get(f"/api/courses/{course['id']}")).json()
    assert len(detail["chapters"]) == 1  # written exactly once


async def test_the_notes_come_first_completed_by_the_slides(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Controlli", "language": "it"})).json()
    job_id = await lesson(admin, course["id"], _slides(["Schema in retroazione", "Diagramma di Bode"]),
                          ["- errore a regime nullo con azione integrale\n- margine di fase"], title="Retroazione")
    j = await _done(admin, job_id)
    groups = j["result"]["groups"]
    assert len(groups) == 1 and groups[0]["notes"] == ["retroazione-appunti.md"] and groups[0]["group"] == "Retroazione"
    ch = (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"][0]
    text = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    # The notes first (the backbone), completed by the slides.
    assert text.index("azione integrale") < text.index("Diagramma di Bode")


async def test_a_long_text_is_written_in_halves_when_cut_off(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Metà", "language": "it"})).json()
    job_id = await lesson(admin, course["id"], notes=["## Uno\n\n- primo punto FAKE:TRUNCATE\n\n## Due\n\n- secondo punto"])
    j = await _done(admin, job_id)
    assert any("two halves" in x["message"] for x in j["logs"])
    ch = (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"][0]
    text = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    assert "primo punto" in text and "secondo punto" in text
