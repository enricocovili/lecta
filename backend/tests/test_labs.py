"""Laboratories: a lesson's lab with the files explained in class, recognised from their bytes and names, never executed."""

from __future__ import annotations

import json
import uuid

import pytest

from app.services import labs as lb

from .test_lessons import new_lesson, slides_pdf

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
NOTEBOOK = json.dumps({"cells": [{"cell_type": "code", "source": ["print(1)"]}], "metadata": {"language_info": {"name": "python"}}, "nbformat": 4})


async def upload(admin, lid, path, data: bytes | str):
    body = data.encode() if isinstance(data, str) else data
    return await admin.post(f"/api/lessons/{lid}/lab/files", params={"path": path}, content=body, headers={"content-type": "application/octet-stream"})


async def new_lab(admin):
    course, lesson = await new_lesson(admin, n=0)
    r = await admin.post(f"/api/lessons/{lesson['id']}/lab")
    assert r.status_code == 201, r.text
    return course, lesson, r.json()


# --------------------------------------------------------------------------- units


def test_paths_are_cleaned():
    assert lb.clean_path(" src\\main.c ") == "src/main.c"
    assert lb.clean_path("./a//b/./c.py") == "a/b/c.py"
    for bad in ("", "/", "../etc/passwd", "a/../../b", "a\x00b", "con:1", "x" * 400):
        with pytest.raises(lb.PathError):
            lb.clean_path(bad)


def test_files_are_recognised_by_their_bytes_and_names():
    assert (c := lb.classify("main.c", b"int main(void) { return 0; }\r\n")).kind == "text" and c.language == "c" and c.content.endswith("}\n")
    assert lb.classify("Makefile", b"all:\n\tcc main.c\n").language == "makefile"
    assert lb.classify("query.sql", "SELECT 'è' FROM t;".encode("cp1252")).content == "SELECT 'è' FROM t;"
    assert lb.classify("notes", b"plain").language == "text"
    assert (nb := lb.classify("lab.ipynb", NOTEBOOK.encode())).kind == "notebook" and nb.language == "python"
    assert lb.classify("broken.ipynb", b"{not json").kind == "text"
    assert lb.classify("slides.pdf", slides_pdf(1)).kind == "pdf"
    assert lb.classify("photo.png", PNG).kind == "image"
    # A picture by name only is not a picture; archives and executables stay opaque.
    assert lb.classify("fake.png", b"not an image").kind == "text"
    assert lb.classify("proj.zip", b"PK\x03\x04rest").kind == "binary"
    assert lb.classify("a.out", b"\x7fELF\x02\x01\x01\x00\x00\x00").kind == "binary"
    # An HTML or SVG page is source code to read, never a page to render.
    assert lb.classify("index.html", b"<script>alert(1)</script>").kind == "text"
    assert lb.classify("draw.svg", b"<svg onload='x()'/>").language == "xml"


def test_comment_anchors_are_validated():
    assert lb.clean_anchor({}) == {} and lb.clean_anchor(None) == {}
    assert lb.clean_anchor({"from": 3, "to": 5, "text": "int x;", "extra": 1}) == {"from": 3, "to": 5, "text": "int x;"}
    assert lb.clean_anchor({"from": 2}) == {"from": 2, "to": 2, "text": ""}
    for bad in ([], "x", {"from": 0}, {"from": 5, "to": 3}, {"from": "1"}, {"from": True}, {"from": 1, "text": 3}, {"page": 1}):
        with pytest.raises(lb.AnchorError):
            lb.clean_anchor(bad)


# --------------------------------------------------------------------------- the API


async def test_a_lab_belongs_to_its_lesson(admin):
    course, lesson = await new_lesson(admin, n=0)
    lid = lesson["id"]
    assert lesson["lab"] is None
    assert (await admin.get(f"/api/lessons/{lid}/lab")).status_code == 404
    assert (await upload(admin, lid, "a.c", "x")).status_code == 404  # no lab yet

    first = (await admin.post(f"/api/lessons/{lid}/lab")).json()
    again = (await admin.post(f"/api/lessons/{lid}/lab")).json()
    assert first["id"] == again["id"] and first["files"] == []
    assert first["lesson"]["title"] == "Lezione 5" and first["lesson"]["course_name"] == "Teoria dei Segnali"
    by_number = (await admin.get(f"/api/courses/{course['id']}/lessons/{lesson['number']}/lab")).json()
    assert by_number["id"] == first["id"]
    await upload(admin, lid, "a.c", "int x;")
    assert (await admin.get(f"/api/lessons/{lid}")).json()["lab"] == {"files": 1, "comments": 0}

    # The lab goes with its lesson.
    assert (await admin.delete(f"/api/lessons/{lid}")).status_code == 200
    assert (await admin.get(f"/api/lessons/{lid}/lab")).status_code == 404


async def test_files_are_uploaded_read_renamed_and_deleted(admin):
    _, lesson, _ = await new_lab(admin)
    lid = lesson["id"]
    r = await upload(admin, lid, "src/main.c", "#include <stdio.h>\nint main(void) {\n  printf(\"ciao\\n\");\n}\n")
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["path"] == "src/main.c" and f["kind"] == "text" and f["language"] == "c" and f["version"] == 1 and not f["replaced"]
    await upload(admin, lid, "lab.ipynb", NOTEBOOK)
    await upload(admin, lid, "esercizi.pdf", slides_pdf(2))
    await upload(admin, lid, "schema.png", PNG)

    lab = (await admin.get(f"/api/lessons/{lid}/lab")).json()
    assert [(x["path"], x["kind"]) for x in lab["files"]] == [("esercizi.pdf", "pdf"), ("lab.ipynb", "notebook"), ("schema.png", "image"), ("src/main.c", "text")]
    text = (await admin.get(f"/api/lessons/{lid}/lab/files/{f['id']}")).json()
    assert text["content"].startswith("#include") and text["size"] == len(text["content"].encode())
    pdf = next(x for x in lab["files"] if x["kind"] == "pdf")
    assert (await admin.get(f"/api/lessons/{lid}/lab/files/{pdf['id']}")).json()["content"] is None

    # Uploading the same name again replaces the file.
    r = (await upload(admin, lid, "src/main.c", "int main(void) { return 1; }\n")).json()
    assert r["id"] == f["id"] and r["version"] == 2 and r["replaced"]
    assert len((await admin.get(f"/api/lessons/{lid}/lab")).json()["files"]) == 4

    # Renaming follows the new name's language; a taken name or a bad one is refused.
    r = await admin.patch(f"/api/lessons/{lid}/lab/files/{f['id']}", json={"path": "src/main.py"})
    assert r.status_code == 200 and r.json()["language"] == "python"
    assert (await admin.patch(f"/api/lessons/{lid}/lab/files/{f['id']}", json={"path": "lab.ipynb"})).status_code == 409
    assert (await admin.patch(f"/api/lessons/{lid}/lab/files/{f['id']}", json={"path": "../x"})).status_code == 400

    assert (await admin.delete(f"/api/lessons/{lid}/lab/files/{f['id']}")).status_code == 200
    assert (await admin.get(f"/api/lessons/{lid}/lab/files/{f['id']}")).status_code == 404


async def test_uploads_are_bounded(admin, monkeypatch):
    _, lesson, _ = await new_lab(admin)
    lid = lesson["id"]
    assert (await upload(admin, lid, "../../etc/passwd", "x")).status_code == 400
    monkeypatch.setattr(lb, "MAX_FILE_BYTES", 1000)
    r = await upload(admin, lid, "big.c", "x" * 1001)
    assert r.status_code == 413 and "big.c" in r.json()["detail"]
    assert (await upload(admin, lid, "ok.c", "x" * 1000)).status_code == 200
    monkeypatch.setattr(lb, "MAX_FILES", 1)
    assert (await upload(admin, lid, "two.c", "y")).status_code == 409
    assert (await upload(admin, lid, "ok.c", "z")).status_code == 200  # replacing is not a new file


async def test_downloads_are_attachments_that_never_run(admin):
    _, lesson, _ = await new_lab(admin)
    lid = lesson["id"]
    html = (await upload(admin, lid, "pagina.html", "<script>alert(1)</script>")).json()
    png = (await upload(admin, lid, "schema.png", PNG)).json()
    pdf = (await upload(admin, lid, "testo esercizi.pdf", slides_pdf(1))).json()

    r = await admin.get(f"/api/lessons/{lid}/lab/files/{html['id']}/raw")
    assert r.status_code == 200 and r.content == b"<script>alert(1)</script>"
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("attachment;") and "sandbox" in r.headers["content-security-policy"]
    r = await admin.get(f"/api/lessons/{lid}/lab/files/{png['id']}/raw")
    assert r.headers["content-type"] == "image/png" and r.content == PNG and r.headers["content-disposition"].startswith("attachment;")
    r = await admin.get(f"/api/lessons/{lid}/lab/files/{pdf['id']}/raw")
    assert r.headers["content-type"] == "application/pdf" and "testo%20esercizi.pdf" in r.headers["content-disposition"]


async def test_another_lessons_file_is_not_reachable(admin):
    _, a, _ = await new_lab(admin)
    _, b, _ = await new_lab(admin)
    f = (await upload(admin, a["id"], "a.c", "int a;")).json()
    assert (await admin.get(f"/api/lessons/{b['id']}/lab/files/{f['id']}")).status_code == 404
    assert (await admin.get(f"/api/lessons/{b['id']}/lab/files/{f['id']}/raw")).status_code == 404
    assert (await admin.delete(f"/api/lessons/{b['id']}/lab/files/{f['id']}")).status_code == 404


async def test_comments_are_written_live_and_never_doubled(admin):
    _, lesson, _ = await new_lab(admin)
    lid = lesson["id"]
    f = (await upload(admin, lid, "main.c", "int a;\nint b;\nint c;\n")).json()
    other = (await upload(admin, lid, "other.c", "x")).json()
    cid = str(uuid.uuid4())
    url = f"/api/lessons/{lid}/lab/comments/{cid}"

    r = await admin.put(url, json={"file_id": f["id"], "anchor": {"from": 2, "to": 3, "text": "int b;\nint c;"}, "body": "Due variabili $x^2$"})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and r.json()["anchor"]["from"] == 2
    # The same comment sent again (a retry, an edit) is the same comment.
    r = await admin.put(url, json={"file_id": f["id"], "anchor": {"from": 2, "to": 3, "text": "int b;\nint c;"}, "body": "Due variabili, poi una terza"})
    assert r.json()["version"] == 2
    whole = str(uuid.uuid4())
    await admin.put(f"/api/lessons/{lid}/lab/comments/{whole}", json={"file_id": f["id"], "body": "Il file del primo esercizio"})
    lab = (await admin.get(f"/api/lessons/{lid}/lab")).json()
    assert [(c["id"], c["body"]) for c in lab["comments"]] == [(cid, "Due variabili, poi una terza"), (whole, "Il file del primo esercizio")]
    assert (await admin.get(f"/api/lessons/{lid}")).json()["lab"] == {"files": 2, "comments": 2}

    # A comment stays on its file, its id must be an id, its lines must make sense.
    assert (await admin.put(url, json={"file_id": other["id"], "body": "x"})).status_code == 404
    assert (await admin.put(f"/api/lessons/{lid}/lab/comments/not-an-id", json={"file_id": f["id"]})).status_code == 422
    assert (await admin.put(f"/api/lessons/{lid}/lab/comments/{uuid.uuid4()}", json={"file_id": f["id"], "anchor": {"from": 4, "to": 1}})).status_code == 422
    assert (await admin.put(f"/api/lessons/{lid}/lab/comments/{uuid.uuid4()}", json={"file_id": 999999})).status_code == 404

    # Deleting twice is fine; deleting the file takes its comments away.
    assert (await admin.delete(url)).status_code == 200
    assert (await admin.delete(url)).status_code == 200
    await admin.delete(f"/api/lessons/{lid}/lab/files/{f['id']}")
    assert (await admin.get(f"/api/lessons/{lid}/lab")).json()["comments"] == []


async def test_another_labs_comment_is_not_reachable(admin):
    _, a, _ = await new_lab(admin)
    _, b, _ = await new_lab(admin)
    fa = (await upload(admin, a["id"], "a.c", "int a;")).json()
    fb = (await upload(admin, b["id"], "b.c", "int b;")).json()
    cid = str(uuid.uuid4())
    await admin.put(f"/api/lessons/{a['id']}/lab/comments/{cid}", json={"file_id": fa["id"], "body": "mio"})
    assert (await admin.put(f"/api/lessons/{b['id']}/lab/comments/{cid}", json={"file_id": fb["id"], "body": "rubato"})).status_code == 404
    await admin.delete(f"/api/lessons/{b['id']}/lab/comments/{cid}")
    assert [c["body"] for c in (await admin.get(f"/api/lessons/{a['id']}/lab")).json()["comments"]] == ["mio"]
