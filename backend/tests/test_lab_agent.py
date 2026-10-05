"""The lab's assistant: it reads the lab, explains, edits the files and comments them when asked; one click undoes a turn."""

from __future__ import annotations

import uuid

from .test_agent import _events
from .test_ingest_e2e import setup_fake
from .test_labs import new_lab, upload

MAIN_C = "#include <stdio.h>\nint main(void) {\n  return 0;\n}\n"


async def _lab(admin):
    await setup_fake(admin)
    course, lesson, lab = await new_lab(admin)
    f = (await upload(admin, lesson["id"], "main.c", MAIN_C)).json()
    cid = str(uuid.uuid4())
    await admin.put(f"/api/lessons/{lesson['id']}/lab/comments/{cid}", json={"file_id": f["id"], "anchor": {"from": 2, "to": 2, "text": "int main(void) {"}, "body": "il main"})
    s = (await admin.post("/api/chat/sessions", json={"course_id": course["id"], "lab_id": lab["id"]})).json()
    return course, lesson, lab, f, cid, s


async def _ask(admin, s, text, **scope):
    r = await admin.post(f"/api/chat/sessions/{s['id']}/messages", json={"content": text, "scope": scope})
    assert r.status_code == 200, r.text
    reply = r.json()["reply"]
    return reply, await _events(admin, reply["id"])


async def _state(admin, lesson, f):
    lab = (await admin.get(f"/api/lessons/{lesson['id']}/lab")).json()
    content = (await admin.get(f"/api/lessons/{lesson['id']}/lab/files/{f['id']}")).json()["content"]
    return lab, content


async def test_the_lab_has_its_own_conversations(admin):
    course, lesson, lab, f, _, s = await _lab(admin)
    assert s["lab_id"] == lab["id"]
    course_session = (await admin.post("/api/chat/sessions", json={"course_id": course["id"]})).json()
    assert [x["id"] for x in (await admin.get("/api/chat/sessions", params={"course_id": course["id"]})).json()] == [course_session["id"]]
    assert [x["id"] for x in (await admin.get("/api/chat/sessions", params={"course_id": course["id"], "lab_id": lab["id"]})).json()] == [s["id"]]
    # A lab of another course, a file of another lab, a review: refused.
    other_course, _, other_lab = await new_lab(admin)
    assert (await admin.post("/api/chat/sessions", json={"course_id": course["id"], "lab_id": other_lab["id"]})).status_code == 404
    _, other_lesson, _ = await new_lab(admin)
    g = (await upload(admin, other_lesson["id"], "x.c", "int x;")).json()
    r = await admin.post(f"/api/chat/sessions/{s['id']}/messages", json={"content": "x", "scope": {"file_id": g["id"]}})
    assert r.status_code == 404
    r = await admin.post(f"/api/chat/sessions/{s['id']}/messages", json={"content": "x", "scope": {"mode": "review"}})
    assert r.status_code == 422


async def test_it_explains_selected_lines_without_touching_anything(admin):
    _, lesson, _, f, _, s = await _lab(admin)
    reply, events = await _ask(admin, s, "Cosa fa questa riga?", file_id=f["id"], mode="explain", selection={"from_line": 3, "to_line": 3, "text": "  return 0;"})
    assert [d["name"] for e, d in events if e == "tool"] == ["read_lab_file"]
    final = next(d for e, d in events if e == "done")["reply"]
    assert "return 0;" in final["content"] and final["change"] is None
    assert (await _state(admin, lesson, f))[1] == MAIN_C


async def test_an_edit_is_applied_at_once_and_undone_in_one_click(admin):
    _, lesson, _, f, cid, s = await _lab(admin)
    reply, events = await _ask(admin, s, "Aggiungi un commento in testa al file", file_id=f["id"], mode="edit")
    assert [d["name"] for e, d in events if e == "tool"] == ["read_lab_file", "edit_lab_file"]
    change = next(d for e, d in events if e == "change")["files"][0]
    assert change["path"] == "main.c" and change["op"] == "modify" and change["added"] == 1 and change["file_id"] == f["id"]
    lab, content = await _state(admin, lesson, f)
    assert content.startswith("// Modificato dall'assistente\n#include")
    # The comment followed its line.
    assert next(c for c in lab["comments"] if c["id"] == cid)["anchor"]["from"] == 3

    r = await admin.post(f"/api/chat/replies/{reply['id']}/undo")
    assert r.status_code == 200 and r.json()["restored"] == ["main.c"]
    lab, content = await _state(admin, lesson, f)
    assert content == MAIN_C and next(c for c in lab["comments"] if c["id"] == cid)["anchor"]["from"] == 2
    assert (await admin.post(f"/api/chat/replies/{reply['id']}/undo")).status_code == 409


async def test_comments_and_new_files_come_and_go_with_the_turn(admin):
    _, lesson, _, f, _, s = await _lab(admin)
    reply, events = await _ask(admin, s, "Commenta la prima riga", file_id=f["id"])
    assert [d["name"] for e, d in events if e == "tool"] == ["read_lab_file", "add_comment"]
    change = next(d for e, d in events if e == "done")["reply"]["change"]
    assert change["files"] == [{"path": "main.c", "file_id": f["id"], "chapter_id": None, "op": "comment", "added": 0, "removed": 0, "hunks": [], "comments": 1}]
    lab, _ = await _state(admin, lesson, f)
    assert [c["anchor"] for c in lab["comments"] if c["body"].startswith("Qui inizia")] == [{"from": 1, "to": 1, "text": "#include <stdio.h>"}]
    await admin.post(f"/api/chat/replies/{reply['id']}/undo")
    assert len((await _state(admin, lesson, f))[0]["comments"]) == 1

    reply, events = await _ask(admin, s, "Scrivi la soluzione in un file", file_id=f["id"])
    assert next(d for e, d in events if e == "change")["files"][0]["op"] == "create"
    assert "soluzione.txt" in [x["path"] for x in (await _state(admin, lesson, f))[0]["files"]]
    await admin.post(f"/api/chat/replies/{reply['id']}/undo")
    assert "soluzione.txt" not in [x["path"] for x in (await _state(admin, lesson, f))[0]["files"]]


async def test_undo_is_refused_when_the_file_changed_since(admin):
    _, lesson, _, f, _, s = await _lab(admin)
    reply, _ = await _ask(admin, s, "Modifica il file", file_id=f["id"], mode="edit")
    current = (await admin.get(f"/api/lessons/{lesson['id']}/lab/files/{f['id']}")).json()
    await admin.put(f"/api/lessons/{lesson['id']}/lab/files/{f['id']}/content", json={"content": current["content"] + "// mia\n", "base_version": current["version"]})
    r = await admin.post(f"/api/chat/replies/{reply['id']}/undo")
    assert r.status_code == 409 and r.json()["detail"]["skipped"] == ["main.c"]
