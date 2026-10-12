"""Whose content is whose: editors see their own courses and the published ones, viewers and admins everything.

The sweeps call every route under a course (its chapters and files, its lessons and labs, what its imports read) as
another editor: on an unpublished course everything is 404 (an editor can't tell it exists); on a published one the
text reads and changes are 403 (but for draft notes, which any reader writes), while lessons, labs, import material,
jobs and conversations stay 404 (they are never published).
"""

from __future__ import annotations

import uuid

from app.db import SessionLocal
from app.models import ChatMessage, IngestFigure, IngestItem, Job, SourceFile, Upload
from app.security import access

from .test_access import _calls, _private, _table
from .test_boundary import PARAM_RE

# Path parameters that lead to a course (filled with the material's ids; any other parameter is 1). `sid` is a
# source file, or under /api/chat/ a conversation.
COURSE_PARAMS = {"course_id", "chapter_id", "lesson_id", "number", "sid", "item_id", "fig_id", "job_id", "rid"}


async def _course(client, published: bool = False, admin=None) -> dict:  # noqa: ANN001
    r = await client.post("/api/courses", json={"name": f"Corso {uuid.uuid4().hex[:6]}", "chapters": ["Uno"]})
    assert r.status_code == 201, r.text
    course = r.json()
    if published:
        assert (await admin.post(f"/api/courses/{course['id']}/publish")).status_code == 200
    return course


async def _material(client, published: bool = False, admin=None) -> dict:  # noqa: ANN001
    """A course with a lesson, its lab and an import's sources, items and figures: the ids of each path parameter."""
    course = await _course(client, published, admin)
    r = await client.post("/api/lessons", json={"course_id": course["id"], "title": "Lezione"})
    assert r.status_code == 201, r.text
    lesson = r.json()
    assert (await client.post(f"/api/lessons/{lesson['id']}/lab")).status_code == 201
    async with SessionLocal() as db:
        job = Job(kind="ingest", title="Lezione", status="succeeded", course_id=course["id"])
        db.add(job)
        await db.flush()
        up = Upload(status="done", target_course_id=course["id"], job_id=job.id, via="lesson")
        db.add(up)
        await db.flush()
        sf = SourceFile(upload_id=up.id, name="slide.pdf", kind="pdf")
        db.add(sf)
        await db.flush()
        item = IngestItem(job_id=job.id, upload_id=up.id, source_file_id=sf.id, key="i1", position=1, kind="slide")
        db.add(item)
        await db.flush()
        fig = IngestFigure(job_id=job.id, item_id=item.id, key="f1", origin="image", source_ref="slide.pdf#1")
        db.add(fig)
        await db.commit()
        ids = {"sid": sf.id, "item_id": item.id, "fig_id": fig.id, "job_id": job.id}
    r = await client.post("/api/chat/sessions", json={"course_id": course["id"]})
    assert r.status_code == 201, r.text
    chat = r.json()
    async with SessionLocal() as db:
        reply = ChatMessage(session_id=chat["id"], role="assistant", content="Fatto.", status="done")
        db.add(reply)
        await db.commit()
        ids |= {"chat_sid": chat["id"], "rid": reply.id}
    return {"course": course, "course_id": course["id"], "chapter_id": course["chapters"][0]["id"],
            "lesson_id": lesson["id"], "number": lesson["number"], **ids}


def _course_routes(app):  # noqa: ANN001
    for r, method in _private(app):
        params = {m.group(1) for m in PARAM_RE.finditer(r.path)}
        if params & COURSE_PARAMS:
            yield r, method


def _is_private(route) -> bool:  # noqa: ANN001
    """Lessons, labs, import material, jobs and conversations: never opened by publishing."""
    calls = set(_calls(route.dependant))
    return bool({access.private_access, access.job_access} & calls) or (
        access.chat_access in calls and ("{sid}" in route.path or "{rid}" in route.path))


def _url(path: str, ids: dict) -> str:
    if path.startswith("/api/chat/"):
        ids = {**ids, "sid": ids["chat_sid"]}
    return PARAM_RE.sub(lambda m: str(ids.get(m.group(1), 1)), path)


async def test_a_course_belongs_to_whoever_created_it(editor, admin):
    course = await _course(editor)
    assert course["can_write"] and course["owner"] == "editor"
    mine = (await editor.get("/api/courses")).json()
    assert any(c["id"] == course["id"] and c["can_write"] for c in mine)
    # The admin sees it, and may change it.
    r = await admin.get(f"/api/courses/{course['id']}")
    assert r.status_code == 200 and r.json()["owner"] == "editor" and r.json()["can_write"]
    assert (await admin.patch(f"/api/courses/{course['id']}", json={"description": "dall'admin"})).status_code == 200


async def test_another_editor_does_not_see_an_unpublished_course(editor, other_editor):
    course = await _course(editor)
    assert all(c["id"] != course["id"] for c in (await other_editor.get("/api/courses")).json())
    assert all(c["id"] != course["id"] for c in (await other_editor.get("/api/tree")).json())
    assert (await other_editor.get(f"/api/courses/{course['id']}")).status_code == 404


async def test_another_editor_reads_a_published_course_but_cannot_change_it(editor, other_editor, admin):
    course = await _course(editor, published=True, admin=admin)
    listed = [c for c in (await other_editor.get("/api/courses")).json() if c["id"] == course["id"]]
    assert listed and not listed[0]["can_write"] and listed[0]["owner"] == "editor"
    # The sidebar and the home page say whose it is, and that it is read-only.
    tree = [c for c in (await other_editor.get("/api/tree")).json() if c["id"] == course["id"]]
    assert tree and not tree[0]["can_write"] and tree[0]["owner"] == "editor"
    home = [c for c in (await other_editor.get("/api/dashboard")).json()["courses"] if c["id"] == course["id"]]
    assert home and not home[0]["can_write"] and home[0]["owner"] == "editor"
    assert (await other_editor.get(f"/api/courses/{course['id']}")).status_code == 200
    r = await other_editor.patch(f"/api/courses/{course['id']}", json={"description": "no"})
    assert r.status_code == 403


async def test_a_viewer_reads_every_course_and_changes_none(editor, viewer):
    course = await _course(editor)
    assert any(c["id"] == course["id"] and not c["can_write"] for c in (await viewer.get("/api/courses")).json())
    assert (await viewer.get(f"/api/courses/{course['id']}/chapters/{course['chapters'][0]['id']}/draft")).status_code == 200
    assert (await viewer.patch(f"/api/courses/{course['id']}", json={"description": "no"})).status_code == 403


async def test_every_route_of_an_unpublished_course_is_404_to_another_editor(app, editor, other_editor):
    ids = await _material(editor)
    table = _table()
    for r, method in _course_routes(app):
        url = _url(r.path, ids)
        res = await other_editor.request(method, url)
        want = 403 if table[(method, r.path)] == "admin" else 404  # admin-only routes check the role first
        assert res.status_code == want, f"{method} {url} -> {res.status_code} {res.text[:200]}"


async def test_every_change_to_a_published_course_is_403_to_another_editor(app, editor, other_editor, admin):
    ids = await _material(editor, published=True, admin=admin)
    table = _table()
    for r, method in _course_routes(app):
        url = _url(r.path, ids)
        res = await other_editor.request(method, url)
        if table[(method, r.path)] == "admin":
            assert res.status_code == 403, f"{method} {url} -> {res.status_code}"
        elif _is_private(r):
            assert res.status_code == 404, f"{method} {url} -> {res.status_code} {res.text[:200]}"
        elif method == "GET":
            assert res.status_code != 403, f"{method} {url} -> 403"
        elif r.path.endswith("/notes"):  # any reader writes notes on a published course (here: a body is missing)
            assert res.status_code == 422, f"{method} {url} -> {res.status_code} {res.text[:200]}"
        else:
            assert res.status_code == 403, f"{method} {url} -> {res.status_code} {res.text[:200]}"


async def test_lessons_and_labs_are_their_course_owners(editor, other_editor, viewer, admin):
    ids = await _material(editor, published=True, admin=admin)
    lesson, course_id = ids["lesson_id"], ids["course_id"]
    for client, writes in ((editor, True), (viewer, False), (admin, True)):
        r = await client.get(f"/api/lessons/{lesson}")
        assert r.status_code == 200
        # The editor learns from the lesson whether it may change it: the viewer gets it read-only.
        assert r.json()["can_write"] is writes
        assert (await client.get(f"/api/courses/{course_id}/lessons/{ids['number']}")).json()["can_write"] is writes
        # So does the lab, by the lesson and by its address.
        r = await client.get(f"/api/lessons/{lesson}/lab")
        assert r.status_code == 200 and r.json()["can_write"] is writes
        assert (await client.get(f"/api/courses/{course_id}/lessons/{ids['number']}/lab")).json()["can_write"] is writes
        assert (await client.get(f"/api/sources/{ids['sid']}")).status_code == 200
        assert (await client.get(f"/api/jobs/{ids['job_id']}/ingest")).status_code == 200
        assert [l["can_write"] for l in (await client.get("/api/lessons")).json() if l["id"] == lesson] == [writes]
    # Another editor doesn't see them, even on a published course, nor can put a lesson into it.
    assert all(l["id"] != lesson for l in (await other_editor.get("/api/lessons")).json())
    assert all(l["id"] != lesson for l in (await other_editor.get(f"/api/lessons?course_id={course_id}")).json())
    assert (await other_editor.post("/api/lessons", json={"course_id": course_id, "title": "Intrusa"})).status_code == 404
    theirs = await _material(other_editor)
    r = await other_editor.patch(f"/api/lessons/{theirs['lesson_id']}", json={"course_id": course_id})
    assert r.status_code == 404


async def _write(client, course: dict, text: str) -> None:  # noqa: ANN001
    path = course["chapters"][0]["path"]
    r = await client.put(f"/api/courses/{course['id']}/files/content", json={"path": path, "content": text})
    assert r.status_code == 200, r.text


async def test_lists_search_and_export_show_only_what_one_may_read(editor, other_editor, viewer, admin):
    word = "zx" + uuid.uuid4().hex[:8]
    ids = await _material(editor)
    course = ids["course"]
    await _write(editor, course, f"\\section{{Prova}}\n{word} compare solo qui.\n")
    slug = course["slug"]

    def found(res) -> bool:  # noqa: ANN001
        return any(f["course_id"] == course["id"] for f in res.json()["files"])

    for client, sees in ((editor, True), (viewer, True), (admin, True), (other_editor, False)):
        assert found(await client.get(f"/api/search?q={word}")) is sees
        assert any(j["id"] == ids["job_id"] for j in (await client.get("/api/jobs")).json()) is sees
        home = (await client.get("/api/dashboard")).json()
        assert any(c["id"] == course["id"] for c in home["courses"]) is sees
        assert any(d["course_id"] == course["id"] for d in home["recent_documents"]) is sees
        export = await client.get("/api/export")
        assert (f"{slug}/".encode() in export.content) is sees
    # Bulk deleting the finished jobs deletes only one's own.
    r = await other_editor.post("/api/jobs/delete", json={"status": "succeeded"})
    assert ids["job_id"] not in r.json()["deleted"]
    assert (await editor.get(f"/api/jobs/{ids['job_id']}")).status_code == 200
    # Published, the text is found by everybody; the job stays the owner's.
    assert (await admin.post(f"/api/courses/{course['id']}/publish")).status_code == 200
    assert found(await other_editor.get(f"/api/search?q={word}"))
    assert all(j["id"] != ids["job_id"] for j in (await other_editor.get("/api/jobs")).json())


async def test_a_job_belongs_to_whoever_started_it(editor, admin):
    ids = await _material(editor)
    r = await editor.post(f"/api/courses/{ids['course_id']}/publish")
    assert r.status_code == 403  # publishing is the admins'
    r = await admin.post(f"/api/courses/{ids['course_id']}/publish")
    job = (await admin.get(f"/api/jobs/{r.json()['job_id']}")).json()
    assert job["user_id"] is not None and job["course_id"] == ids["course_id"]
    # The course's owner sees the job the admin started on it.
    assert (await editor.get(f"/api/jobs/{job['id']}")).status_code == 200


async def test_a_conversation_is_its_authors(editor, other_editor, viewer, admin):
    ids = await _material(editor, published=True, admin=admin)
    course, sid = ids["course_id"], ids["chat_sid"]
    mine = (await editor.get(f"/api/chat/sessions?course_id={course}")).json()
    assert [s["id"] for s in mine] == [sid] and mine[0]["author"] == "editor"
    assert (await other_editor.get(f"/api/chat/sessions?course_id={course}")).json() == []
    assert (await other_editor.get(f"/api/chat/sessions/{sid}")).status_code == 404
    # Admins see everybody's, with the author.
    assert any(s["id"] == sid and s["author"] == "editor" for s in (await admin.get(f"/api/chat/sessions?course_id={course}")).json())
    assert (await admin.get(f"/api/chat/sessions/{sid}")).json()["author"] == "editor"
    # The assistant only works for whoever may change the course.
    assert (await other_editor.post("/api/chat/sessions", json={"course_id": course})).status_code == 403
    assert (await viewer.post("/api/chat/sessions", json={"course_id": course})).status_code == 403
    unpublished = await _course(editor)
    assert (await other_editor.post("/api/chat/sessions", json={"course_id": unpublished["id"]})).status_code == 404


async def test_ai_calls_and_costs_are_split_by_user(editor, admin):
    from app.egress import gate
    from app.models import AuditLog

    ids = await _material(editor)
    async with SessionLocal() as db:
        user_id = (await editor.get("/api/courses/" + str(ids["course_id"]))).json()["owner_id"]
        assert await gate._caller(db, gate.GateContext(chat_session_id=ids["chat_sid"])) == user_id
        job = Job(kind="ingest", title="x", status="succeeded", course_id=ids["course_id"], user_id=user_id)
        db.add(job)
        await db.flush()
        assert await gate._caller(db, gate.GateContext(job_id=job.id)) == user_id
        db.add(AuditLog(provider_name="p", provider_type="fake", model="m", status="ok", user_id=user_id, cost_usd=0.5, tokens_in=10))
        await db.commit()
    users = (await admin.get("/api/costs")).json()["users"]
    assert any(u["username"] == "editor" and u["cost_usd"] >= 0.5 for u in users)
