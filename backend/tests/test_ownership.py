"""Whose content is whose: editors see their own courses and the published ones, viewers and admins everything.

The sweeps call every route under a course as another editor: on an unpublished course everything is 404 (an editor
can't tell it exists), on a published one reads work and changes are 403.
"""

from __future__ import annotations

import uuid

from .test_access import _private, _table
from .test_boundary import PARAM_RE

# Routes under a course that resolve it through other rows (lessons, sources, the assistant's reviews): they get
# their checks with lessons, labs, sources and the assistant, and are left out of the sweeps until then.
NOT_YET = {
    "/api/courses/{course_id}/lessons/{number}",
    "/api/courses/{course_id}/lessons/{number}/lab",
    "/api/courses/{course_id}/sources",
    "/api/courses/{course_id}/chapters/{chapter_id}/sources",
    "/api/courses/{course_id}/review/latest",
}


async def _course(client, published: bool = False, admin=None) -> dict:  # noqa: ANN001
    r = await client.post("/api/courses", json={"name": f"Corso {uuid.uuid4().hex[:6]}", "chapters": ["Uno"]})
    assert r.status_code == 201, r.text
    course = r.json()
    if published:
        assert (await admin.post(f"/api/courses/{course['id']}/publish")).status_code == 200
    return course


def _course_routes(app):  # noqa: ANN001
    for r, method in _private(app):
        if "{course_id}" in r.path and r.path not in NOT_YET:
            yield r, method


def _url(path: str, course: dict) -> str:
    return PARAM_RE.sub(lambda m: str(course["id"]) if m.group(1) == "course_id" else (
        str(course["chapters"][0]["id"]) if m.group(1) == "chapter_id" else "1"), path)


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
    assert (await other_editor.get(f"/api/courses/{course['id']}")).status_code == 200
    r = await other_editor.patch(f"/api/courses/{course['id']}", json={"description": "no"})
    assert r.status_code == 403


async def test_a_viewer_reads_every_course_and_changes_none(editor, viewer):
    course = await _course(editor)
    assert any(c["id"] == course["id"] and not c["can_write"] for c in (await viewer.get("/api/courses")).json())
    assert (await viewer.get(f"/api/courses/{course['id']}/chapters/{course['chapters'][0]['id']}/draft")).status_code == 200
    assert (await viewer.patch(f"/api/courses/{course['id']}", json={"description": "no"})).status_code == 403


async def test_every_route_of_an_unpublished_course_is_404_to_another_editor(app, editor, other_editor):
    course = await _course(editor)
    table = _table()
    for r, method in _course_routes(app):
        url = _url(r.path, course)
        res = await other_editor.request(method, url)
        want = 403 if table[(method, r.path)] == "admin" else 404  # admin-only routes check the role first
        assert res.status_code == want, f"{method} {url} -> {res.status_code} {res.text[:200]}"


async def test_every_change_to_a_published_course_is_403_to_another_editor(app, editor, other_editor, admin):
    course = await _course(editor, published=True, admin=admin)
    for r, method in _course_routes(app):
        url = _url(r.path, course)
        res = await other_editor.request(method, url)
        if method == "GET":
            assert res.status_code != 403, f"{method} {url} -> 403"
        else:
            assert res.status_code == 403, f"{method} {url} -> {res.status_code} {res.text[:200]}"
