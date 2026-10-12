"""Draft notes: readers comment on and suggest changes to the blocks of a published course; its owner (and admins) see
them all and accept, reject or resolve them (decision 128)."""

from __future__ import annotations

import uuid

from app.services.blocks import split

CHAPTER = """\\chapter{Primo}

Un paragrafo con $x^2$.

\\begin{theorem}
Un enunciato.
\\end{theorem}

Un altro paragrafo.
"""


async def _course(owner, admin, published: bool = True) -> dict:  # noqa: ANN001
    r = await owner.post("/api/courses", json={"name": f"Note {uuid.uuid4().hex[:6]}", "chapters": ["Primo"]})
    assert r.status_code == 201, r.text
    c = r.json()
    ch = c["chapters"][0]
    await _write(owner, c, CHAPTER)
    if published:
        assert (await admin.post(f"/api/courses/{c['id']}/publish")).status_code == 200
    return {"id": c["id"], "ch": ch["id"], "path": ch["path"],
            "url": f"/api/courses/{c['id']}/chapters/{ch['id']}/notes", "all": f"/api/courses/{c['id']}/notes"}


async def _write(client, c: dict, text: str) -> None:  # noqa: ANN001
    path = c["path"] if "path" in c else c["chapters"][0]["path"]
    r = await client.put(f"/api/courses/{c['id']}/files/content", json={"path": path, "content": text})
    assert r.status_code == 200, r.text


async def _source(client, c: dict) -> str:  # noqa: ANN001
    return (await client.get(f"/api/courses/{c['id']}/files/content", params={"path": c["path"]})).json()["content"]


def _block(text: str, start: str) -> dict:
    b = next(b for b in split(text) if b.src.startswith(start))
    return {"start": b.start, "end": b.end, "src": b.src}


async def _note(client, c: dict, kind: str = "comment", on: str = "Un paragrafo", **extra) -> dict:  # noqa: ANN001
    body = {"kind": kind, **_block(CHAPTER, on), **({"body": "Non è chiaro"} if kind == "comment" else {}), **extra}
    r = await client.post(c["url"], json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_readers_comment_and_suggest_on_a_published_course_only(editor, other_editor, viewer, admin):
    c = await _course(editor, admin)
    note = await _note(viewer, c)
    assert note["kind"] == "comment" and note["status"] == "open" and note["author"] == "viewer" and note["mine"]
    assert (note["start"], note["end"], note["changed"]) == (3, 3, False)
    sug = await _note(other_editor, c, "suggestion", on="\\begin{theorem}", proposed="\\begin{theorem}\nUn enunciato vero.\n\\end{theorem}",
                      body="più preciso")
    assert sug["proposed"].startswith("\\begin{theorem}") and sug["body"] == "più preciso" and (sug["start"], sug["end"]) == (5, 7)
    assert (await _note(admin, c))["author"] != "viewer"  # an admin who isn't the owner notes too

    block = _block(CHAPTER, "Un paragrafo")
    # The owner changes their own text instead.
    assert (await editor.post(c["url"], json={"kind": "comment", "body": "io", **block})).status_code == 403
    # A comment needs words, a suggestion its text; the block must be the one shown.
    assert (await viewer.post(c["url"], json={"kind": "comment", "body": "  ", **block})).status_code == 422
    assert (await viewer.post(c["url"], json={"kind": "suggestion", **block})).status_code == 422
    assert (await viewer.post(c["url"], json={"kind": "comment", "body": "x", **block, "end": 4})).status_code == 409
    assert (await viewer.post(c["url"], json={"kind": "comment", "body": "x", **block, "src": "altro"})).status_code == 409

    # Unpublished: nothing new, the notes stay; another editor no longer reads the course at all.
    assert (await admin.post(f"/api/courses/{c['id']}/unpublish")).status_code == 200
    assert (await viewer.post(c["url"], json={"kind": "comment", "body": "x", **block})).status_code == 409
    assert (await other_editor.post(c["url"], json={"kind": "comment", "body": "x", **block})).status_code == 404
    assert (await other_editor.get(c["url"])).status_code == 404
    assert [n["id"] for n in (await viewer.get(c["url"])).json()] == [note["id"]]
    assert len((await editor.get(c["url"])).json()) == 3
    private = await _course(editor, admin, published=False)
    assert (await viewer.post(private["url"], json={"kind": "comment", "body": "x", **block})).status_code == 409


async def test_each_reader_sees_their_own_notes_and_the_owner_and_admins_all(editor, other_editor, viewer, admin):
    c = await _course(editor, admin)
    mine = await _note(viewer, c)
    theirs = await _note(other_editor, c, on="Un altro")

    def ids(res) -> set[int]:  # noqa: ANN001
        assert res.status_code == 200, res.text
        return {n["id"] for n in res.json()}

    for client, sees in ((viewer, {mine["id"]}), (other_editor, {theirs["id"]}), (editor, {mine["id"], theirs["id"]}),
                         (admin, {mine["id"], theirs["id"]})):
        assert ids(await client.get(c["url"])) == sees
        assert ids(await client.get(c["all"])) == sees
    listed = {n["id"]: n for n in (await editor.get(c["url"])).json()}
    assert listed[mine["id"]]["author"] == "viewer" and not listed[mine["id"]]["mine"]
    # Someone else's note doesn't exist for a reader.
    assert (await viewer.patch(f"/api/notes/{theirs['id']}", json={"body": "mio"})).status_code == 404
    assert (await viewer.delete(f"/api/notes/{theirs['id']}")).status_code == 404
    assert (await viewer.post(f"/api/notes/{theirs['id']}/resolve")).status_code == 403  # the role first
    assert (await other_editor.post(f"/api/notes/{mine['id']}/resolve")).status_code == 404


async def test_the_author_edits_and_deletes_a_note_while_it_is_open(editor, viewer, admin):
    c = await _course(editor, admin)
    note = await _note(viewer, c)
    r = await viewer.patch(f"/api/notes/{note['id']}", json={"body": "Ora è chiaro?"})
    assert r.status_code == 200 and r.json()["body"] == "Ora è chiaro?"
    assert (await viewer.patch(f"/api/notes/{note['id']}", json={"body": " "})).status_code == 422
    assert (await viewer.patch(f"/api/notes/{note['id']}", json={"proposed": "x"})).status_code == 422
    # The owner sees it but doesn't write it.
    assert (await editor.patch(f"/api/notes/{note['id']}", json={"body": "no"})).status_code == 403

    r = await editor.post(f"/api/notes/{note['id']}/resolve")
    assert r.status_code == 200 and r.json()["status"] == "resolved" and r.json()["resolved_by"] == "editor"
    assert r.json()["resolved_at"]
    assert (await editor.post(f"/api/notes/{note['id']}/resolve")).status_code == 409
    assert (await viewer.patch(f"/api/notes/{note['id']}", json={"body": "ancora"})).status_code == 409
    assert (await viewer.delete(f"/api/notes/{note['id']}")).status_code == 409
    # The owner (or an admin) clears notes any time.
    assert (await editor.delete(f"/api/notes/{note['id']}")).status_code == 200
    assert (await viewer.get(c["url"])).json() == []

    other = await _note(viewer, c)
    assert (await viewer.delete(f"/api/notes/{other['id']}")).status_code == 200
    assert (await admin.get(c["url"])).json() == []


async def test_accepting_a_suggestion_writes_it_into_the_text(editor, other_editor, viewer, admin):
    c = await _course(editor, admin)
    new = "Un paragrafo con $x^3$, corretto."
    sug = await _note(viewer, c, "suggestion", proposed=new)
    comment = await _note(viewer, c, on="Un altro")
    url = f"/api/notes/{sug['id']}"
    # Only the course's writers close notes, each kind its own way.
    assert (await viewer.post(f"{url}/accept")).status_code == 403
    assert (await editor.post(f"{url}/resolve")).status_code == 409
    assert (await editor.post(f"/api/notes/{comment['id']}/accept")).status_code == 409
    assert (await editor.post(f"/api/notes/{comment['id']}/reject")).status_code == 409

    r = await editor.post(f"{url}/accept")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted" and r.json()["resolved_by"] == "editor"
    assert await _source(editor, c) == CHAPTER.replace("Un paragrafo con $x^2$.", new)
    draft = (await viewer.get(f"/api/courses/{c['id']}/chapters/{c['ch']}/draft")).json()
    assert new in [b["src"] for b in draft["blocks"]]
    assert (await editor.post(f"{url}/accept")).status_code == 409
    assert (await editor.post(f"{url}/reject")).status_code == 409

    # Another editor's suggestion: they can't close it, the owner rejects it, the text stays.
    theirs = await _note(other_editor, c, "suggestion", on="Un altro", proposed="No.")
    assert (await other_editor.post(f"/api/notes/{theirs['id']}/reject")).status_code == 403
    r = await editor.post(f"/api/notes/{theirs['id']}/reject")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert "Un altro paragrafo." in await _source(editor, c)
    # An admin closes notes on anybody's course.
    assert (await admin.post(f"/api/notes/{comment['id']}/resolve")).json()["resolved_by"] != "editor"


async def test_notes_follow_their_block_and_say_when_its_text_changed(editor, viewer, admin):
    c = await _course(editor, admin)
    sug = await _note(viewer, c, "suggestion", on="Un altro", proposed="Un altro paragrafo, meglio.")
    comment = await _note(viewer, c)
    # Two paragraphs written above: both notes move down with their blocks.
    await _write(editor, c, CHAPTER.replace("\\chapter{Primo}\n", "\\chapter{Primo}\n\nNuovo.\n\nAncora nuovo.\n"))
    notes = {n["id"]: n for n in (await viewer.get(c["url"])).json()}
    assert (notes[sug["id"]]["start"], notes[sug["id"]]["changed"]) == (sug["start"] + 4, False)
    assert (notes[comment["id"]]["start"], notes[comment["id"]]["end"]) == (comment["start"] + 4, comment["end"] + 4)
    # Accepting writes where the block is now.
    assert (await editor.post(f"/api/notes/{sug['id']}/accept")).status_code == 200
    assert "Un altro paragrafo, meglio." in await _source(editor, c)

    # The block's own text changed: the note says so, and a suggestion on it can no longer be accepted.
    later = await viewer.post(c["url"], json={"kind": "suggestion", "proposed": "Un paragrafo con $y$.",
                                              **_block(await _source(editor, c), "Un paragrafo")})
    assert later.status_code == 201, later.text
    await _write(editor, c, (await _source(editor, c)).replace("Un paragrafo con $x^2$.", "Un paragrafo riscritto."))
    notes = {n["id"]: n for n in (await editor.get(c["url"])).json()}
    assert notes[comment["id"]]["changed"] and notes[later.json()["id"]]["changed"]
    assert (await editor.post(f"/api/notes/{later.json()['id']}/accept")).status_code == 409
    # Refused, it stays open and nothing was written.
    assert [n["status"] for n in (await editor.get(c["url"])).json() if n["id"] == later.json()["id"]] == ["open"]
    assert "Un paragrafo con $y$." not in await _source(editor, c)
