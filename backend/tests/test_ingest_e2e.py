"""End to end: uploads through the whole import pipeline with the fake provider,
written straight into the course (no approvals, no review). The class notes (.md/.txt)
are the backbone of the text; without them the material is summarised."""

from __future__ import annotations

import tempfile
from pathlib import Path

import fitz
import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import AuditLog

from .conftest import wait_job
from .fixtures import make_all


@pytest.fixture(scope="module")
def fx():
    return make_all(Path(tempfile.mkdtemp(prefix="lecta-fx-")))


async def setup_fake(admin) -> int:
    r = await admin.post("/api/providers", json={"name": "Fake", "type": "fake"})
    pid = r.json()["id"]
    roles = {role: {"primary": {"provider_id": pid, "model": "fake-large"}} for role in
             ("vision", "handwriting", "writing", "classification", "chat")}
    r = await admin.put("/api/settings/roles", json=roles)
    assert r.status_code == 200, r.text
    return pid


async def upload(admin, files: list[tuple[str, bytes]], without_notes: bool = False, **target) -> int:
    up = (await admin.post("/api/uploads", json=target)).json()["id"]
    for name, data in files:
        r = await admin.post(f"/api/uploads/{up}/files", params={"name": name}, content=data, headers={"content-type": "application/octet-stream"})
        assert r.status_code == 200, r.text
    r = await admin.post(f"/api/uploads/{up}/finish", json={"without_notes": without_notes})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


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


async def test_mixed_zip_into_new_course_writes_a_compiling_chapter(admin, worker, fx):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Teoria dei Segnali", "language": "it"})).json()
    job_id = await upload(admin, [("mixed.zip", fx["mixed.zip"].read_bytes())], course_id=course["id"])
    await _done(admin, job_id)

    manifest = (await admin.get(f"/api/jobs/{job_id}/ingest")).json()
    kinds = sorted(i["kind"] for i in manifest["items"])
    assert kinds == ["handwritten", "markdown", "page", "page", "page"], kinds
    files = {f["name"]: f for f in manifest["files"]}
    assert files["setup.exe"]["status"] == "unsupported" and files["._slides.pdf"]["kind"] == "junk"
    origins = sorted(f["origin"] for f in manifest["figures"])
    assert origins == ["drawing", "image", "md_image"], origins
    assert all(f["status"] in ("used", "appended") and f["path"].startswith("images/") for f in manifest["figures"]), manifest["figures"]
    # One folder → one group → one new chapter, written directly.
    assert len(manifest["results"]) == 1 and manifest["results"][0]["type"] == "new_chapter"
    # Reading, then one text written from the notes (no diagrams, no placement: the course was empty).
    tasks = await _tasks(job_id)
    assert set(tasks) == {"read.pages", "read.handwriting", "notes.compose"} and tasks.count("notes.compose") == 1
    assert manifest["results"][0]["notes"] == ["Lezione 3 - Sistemi LTI/note.md"]

    detail = (await admin.get(f"/api/courses/{course['id']}")).json()
    assert len(detail["chapters"]) == 1
    ch = detail["chapters"][0]
    text = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    assert "\\chapter{" in text and "\\lectafigure" not in text and "tikzpicture" not in text
    images = [m for m in text.split("\\lectaimage")[1:]]
    assert len(images) == 3
    tree = (await admin.get(f"/api/courses/{course['id']}/files")).json()["files"]
    paths = {f["path"] for f in tree}
    for part in images:
        path = part.split("{", 1)[1].split("}", 1)[0]
        assert path in paths, (path, paths)
    assert "\\int_{-\\infty}^{+\\infty}" in text  # the Markdown math survived
    assert "\\begin{verbatim}" in text  # Mermaid/ASCII diagrams are kept as code
    sources = (await admin.get(f"/api/courses/{course['id']}/chapters/{ch['id']}/sources")).json()
    assert {s["name"] for s in sources} >= {"slides.pdf", "appunti.jpg", "note.md"}
    res = (await admin.post(f"/api/courses/{course['id']}/compile", json={"full": True})).json()
    assert res["status"] == "ok", [d for d in res["diagnostics"] if d["level"] == "error"][:5]
    pdf = await admin.get(f"/api/courses/{course['id']}/pdf")
    pdf_text = "".join(pg.get_text() for pg in fitz.open(stream=pdf.content, filetype="pdf"))
    assert "Sistemi" in pdf_text


async def test_upload_into_a_chapter_appends(admin, worker, fx):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Segnali e Sistemi", "language": "it", "chapters": ["Sistemi LTI"]})).json()
    ch = course["chapters"][0]
    await admin.put(
        f"/api/courses/{course['id']}/files/content",
        json={"path": ch["path"], "content": "\\chapter{Sistemi LTI}\n\\section{Introduzione}\nUn sistema LTI è lineare e tempo-invariante.\n"},
    )
    job_id = await upload(admin, [("note.md", fx["note.md"].read_bytes()), ("img/schema.png", fx["img/schema.png"].read_bytes())],
                          chapter_id=ch["id"])
    j = await _done(admin, job_id)
    assert j["result"]["groups"][0]["type"] == "append"
    content = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    assert content.index("Un sistema LTI è lineare") < content.index("Lecta: aggiunto da")
    assert "\\lectaimage" in content and "principio di sovrapposizione" in content
    assert await _tasks(job_id) == ["notes.compose"]  # notes alone: nothing to read, one text to write
    detail = (await admin.get(f"/api/courses/{course['id']}")).json()
    assert len(detail["chapters"]) == 1


async def test_bad_latex_gets_one_automatic_fix(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Correzioni", "language": "it"})).json()
    job_id = await upload(admin, [("rotto.pdf", _slides(["Testo con un errore FAKE:BADLATEX"]))], without_notes=True, course_id=course["id"])
    j = await _done(admin, job_id)
    assert (await _tasks(job_id)).count("notes.fix") == 1
    assert j["result"]["groups"][0]["compile"] == "ok"
    ch = (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"][0]
    text = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    assert "\\undefinedmacro" not in text


async def test_truncated_reply_splits_the_unit(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Tagli", "language": "it"})).json()
    job_id = await upload(admin, [("lunga.pdf", _slides(["Primo FAKE:TRUNCATE", "Secondo", "Terzo", "Quarto"]))], without_notes=True,
                          course_id=course["id"])
    j = await _done(admin, job_id)
    assert any("two halves" in x["message"] for x in j["logs"])
    ch = (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"][0]
    text = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    assert "Secondo" in text and "Quarto" in text


async def test_unplaceable_material_goes_to_da_smistare_and_one_click_writes_it(admin, worker, fx):
    await setup_fake(admin)
    md = "# Ricette\n\nGuanciale croccante, pecorino romano, tuorli d'uovo e pepe nero: la carbonara tradizionale.\n"
    job_id = await upload(admin, [("ricetta.md", md.encode())])  # no course chosen, nothing similar in the library
    j = await _done(admin, job_id)
    res = j["result"]["groups"][0]
    assert res["type"] == "inbox"
    course = (await admin.post("/api/courses", json={"name": "Destinazione", "language": "it"})).json()
    r = await admin.post(f"/api/inbox/{res['inbox_id']}/assign", json={"course_id": course["id"]})
    assert r.status_code == 200, r.text
    await _done(admin, r.json()["job_id"])
    detail = (await admin.get(f"/api/courses/{course['id']}")).json()
    assert len(detail["chapters"]) == 1
    item = (await admin.get(f"/api/inbox/{res['inbox_id']}")).json()
    assert item["status"] == "assigned"


async def test_retries_dont_write_twice_or_resend(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Ripetizioni", "language": "it"})).json()
    job_id = await upload(admin, [("uno.pdf", _slides(["Unico contenuto"]))], without_notes=True, course_id=course["id"])
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


async def test_upload_without_notes_asks_first(admin):
    course = (await admin.post("/api/courses", json={"name": "Senza appunti", "language": "it"})).json()
    up = (await admin.post("/api/uploads", json={"course_id": course["id"]})).json()["id"]
    await admin.post(f"/api/uploads/{up}/files", params={"name": "slide.pdf"}, content=_slides(["Uno"]),
                     headers={"content-type": "application/octet-stream"})
    r = await admin.post(f"/api/uploads/{up}/finish", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_notes"
    # The upload is still open: adding the notes is enough.
    await admin.post(f"/api/uploads/{up}/files", params={"name": "appunti.txt"}, content=b"- punto uno\n- punto due\n",
                     headers={"content-type": "application/octet-stream"})
    assert (await admin.post(f"/api/uploads/{up}/finish", json={})).status_code == 200
    # Quick uploads (photos of handwritten notes) don't ask.
    up = (await admin.post("/api/uploads", json={"via": "quick"})).json()["id"]
    await admin.post(f"/api/uploads/{up}/files", params={"name": "slide.pdf"}, content=_slides(["Due"]),
                     headers={"content-type": "application/octet-stream"})
    assert (await admin.post(f"/api/uploads/{up}/finish", json={})).status_code == 200


async def test_notes_and_slides_at_the_root_make_one_text(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Controlli", "language": "it"})).json()
    notes = "# Retroazione\n\n- errore a regime nullo con azione integrale\n- margine di fase\n"
    job_id = await upload(admin, [("Lezione 4.pdf", _slides(["Schema in retroazione", "Diagramma di Bode"])),
                                  ("appunti lezione 4.md", notes.encode())], course_id=course["id"])
    j = await _done(admin, job_id)
    groups = j["result"]["groups"]
    assert len(groups) == 1 and groups[0]["notes"] == ["appunti lezione 4.md"] and groups[0]["group"] == "Retroazione"
    ch = (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"][0]
    text = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    # The notes first (the backbone), completed by the slides.
    assert text.index("azione integrale") < text.index("Diagramma di Bode")


async def test_a_long_text_is_written_in_halves_when_cut_off(admin, worker):
    await setup_fake(admin)
    course = (await admin.post("/api/courses", json={"name": "Metà", "language": "it"})).json()
    notes = "# Uno\n\n- primo punto FAKE:TRUNCATE\n\n# Due\n\n- secondo punto\n"
    job_id = await upload(admin, [("appunti.md", notes.encode())], course_id=course["id"])
    j = await _done(admin, job_id)
    assert any("two halves" in x["message"] for x in j["logs"])
    ch = (await admin.get(f"/api/courses/{course['id']}")).json()["chapters"][0]
    text = (await admin.get(f"/api/courses/{course['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    assert "primo punto" in text and "secondo punto" in text
