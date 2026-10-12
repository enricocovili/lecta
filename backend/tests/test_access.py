"""Who may call each private route: every `/api` route is classified here, by method.

A new route fails `test_every_route_is_classified` until it is added to the table below, so its access level is
always a decision. The levels (`security/auth.py`):

* viewer: any signed-in user (reads of content, their own account);
* editor: editors and admins (writing content, the assistant, imports, compiling);
* admin: admins only (settings, providers, prompts, costs, publishing and lesson share links, diagnostics).

Anonymous callers get 404 on all of them (`test_boundary.py`); a signed-in user below the level gets 403.
"""

from __future__ import annotations

from app.security import auth
from app.security.guard import is_public

from .test_boundary import _all_routes, _fill

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

VIEWER = """
    POST   /api/account/password
    POST   /api/account/totp/setup
    POST   /api/account/totp/enable
    POST   /api/account/totp/disable
    GET    /api/account/sessions
    GET    /api/courses
    GET    /api/courses/{course_id}
    GET    /api/tree
    GET    /api/courses/{course_id}/chapters/{chapter_id}
    GET    /api/dashboard
    GET    /api/dashboard/counts
    GET    /api/jobs
    GET    /api/jobs/{job_id}
    GET    /api/jobs/{job_id}/logs
    GET    /api/jobs/{job_id}/logs.txt
    GET    /api/jobs/{job_id}/problems
    GET    /api/jobs/{job_id}/events
    GET    /api/courses/{course_id}/files
    GET    /api/courses/{course_id}/files/content
    GET    /api/courses/{course_id}/files/raw
    GET    /api/courses/{course_id}/builds/latest
    GET    /api/courses/{course_id}/builds/{build_id}/log
    GET    /api/courses/{course_id}/pdf
    GET    /api/courses/{course_id}/publish
    GET    /api/sources/{sid}
    GET    /api/sources/{sid}/raw
    GET    /api/blobs/items/{item_id}/{which}
    GET    /api/blobs/figures/{fig_id}/{which}
    GET    /api/jobs/{job_id}/ingest
    GET    /api/courses/{course_id}/sources
    GET    /api/courses/{course_id}/chapters/{chapter_id}/sources
    GET    /api/chat/sessions
    GET    /api/chat/sessions/{sid}
    GET    /api/chat/replies/{rid}/events
    GET    /api/courses/{course_id}/review/latest
    GET    /api/search
    GET    /api/export
    GET    /api/concepts
    GET    /api/courses/{course_id}/chapters/{chapter_id}/draft
    GET    /api/courses/{course_id}/draft
    GET    /api/courses/{course_id}/draft/svg/{name}
    GET    /api/courses/{course_id}/source.zip
    GET    /api/lessons
    GET    /api/lessons/{lesson_id}
    GET    /api/courses/{course_id}/lessons/{number}
    GET    /api/lessons/{lesson_id}/pdf
    GET    /api/lessons/{lesson_id}/annotated.pdf
    GET    /api/lessons/{lesson_id}/notes.md
    GET    /api/lessons/{lesson_id}/lab
    GET    /api/courses/{course_id}/lessons/{number}/lab
    GET    /api/lessons/{lesson_id}/lab/files/{file_id}
    GET    /api/lessons/{lesson_id}/lab/files/{file_id}/raw
    GET    /api/account/passkeys
    POST   /api/account/passkeys/options
    POST   /api/account/passkeys
    PATCH  /api/account/passkeys/{passkey_id}
    DELETE /api/account/passkeys/{passkey_id}
"""

EDITOR = """
    POST   /api/courses
    PATCH  /api/courses/{course_id}
    POST   /api/courses/{course_id}/delete
    POST   /api/courses/{course_id}/chapters
    PATCH  /api/courses/{course_id}/chapters/{chapter_id}
    DELETE /api/courses/{course_id}/chapters/{chapter_id}
    POST   /api/courses/{course_id}/chapters/reorder
    POST   /api/jobs/{job_id}/cancel
    POST   /api/jobs/{job_id}/retry
    DELETE /api/jobs/{job_id}
    POST   /api/jobs/delete
    PUT    /api/courses/{course_id}/files/content
    POST   /api/courses/{course_id}/files
    POST   /api/courses/{course_id}/files/rename
    POST   /api/courses/{course_id}/files/delete
    POST   /api/courses/{course_id}/files/upload
    POST   /api/courses/{course_id}/compile
    POST   /api/courses/{course_id}/synctex
    POST   /api/chat/sessions
    DELETE /api/chat/sessions/{sid}
    POST   /api/chat/sessions/{sid}/messages
    POST   /api/chat/replies/{rid}/cancel
    POST   /api/chat/replies/{rid}/undo
    PUT    /api/courses/{course_id}/chapters/{chapter_id}/block
    POST   /api/lessons
    PATCH  /api/lessons/{lesson_id}
    DELETE /api/lessons/{lesson_id}
    POST   /api/lessons/{lesson_id}/slides
    PUT    /api/lessons/{lesson_id}/pages/{page_id}
    PUT    /api/lessons/{lesson_id}/pages/{page_id}/unclear
    PUT    /api/lessons/{lesson_id}/last-page
    POST   /api/lessons/{lesson_id}/pages
    DELETE /api/lessons/{lesson_id}/pages/{page_id}
    POST   /api/lessons/{lesson_id}/pages/restore
    POST   /api/lessons/{lesson_id}/sync
    POST   /api/lessons/{lesson_id}/generate
    POST   /api/lessons/{lesson_id}/lab
    DELETE /api/lessons/{lesson_id}/lab
    PUT    /api/lessons/{lesson_id}/lab/notes
    POST   /api/lessons/{lesson_id}/lab/files
    PUT    /api/lessons/{lesson_id}/lab/files/{file_id}/content
    PATCH  /api/lessons/{lesson_id}/lab/files/{file_id}
    DELETE /api/lessons/{lesson_id}/lab/files/{file_id}
    PUT    /api/lessons/{lesson_id}/lab/comments/{comment_id}
    DELETE /api/lessons/{lesson_id}/lab/comments/{comment_id}
    GET    /api/settings/template/effective
"""

ADMIN = """
    GET    /api/diag/sse
    POST   /api/diag/upload
    POST   /api/diag/jobs/demo
    POST   /api/courses/{course_id}/publish
    POST   /api/courses/{course_id}/unpublish
    GET    /api/settings
    POST   /api/settings/template/reset
    GET    /api/settings/{section}
    PUT    /api/settings/{section}
    POST   /api/settings/embeddings/benchmark
    GET    /api/index/status
    POST   /api/index/rebuild
    GET    /api/providers/types
    GET    /api/providers
    POST   /api/providers
    PATCH  /api/providers/{pid}
    DELETE /api/providers/{pid}
    POST   /api/providers/{pid}/test
    POST   /api/providers/{pid}/models/fetch
    GET    /api/prompts
    PUT    /api/prompts/{key}
    POST   /api/prompts/{key}/reset
    POST   /api/prompts/{key}/versions/{version}/activate
    GET    /api/prompts/{key}/versions/{version}
    GET    /api/audit
    GET    /api/audit/{audit_id}
    GET    /api/costs
    GET    /api/lessons/{lesson_id}/shares
    POST   /api/lessons/{lesson_id}/shares
    DELETE /api/lessons/{lesson_id}/shares/{mode}
    POST   /api/invites
    GET    /api/invites
    DELETE /api/invites/{invite_id}
    GET    /api/users
    PATCH  /api/users/{user_id}
    POST   /api/users/{user_id}/disable
    POST   /api/users/{user_id}/enable
    POST   /api/users/{user_id}/logout
    POST   /api/users/{user_id}/recovery
    DELETE /api/users/{user_id}
"""

# Websockets: the guard needs a session, the route checks the role itself.
WEBSOCKETS = {"/api/diag/ws": "admin"}


def _table() -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for level, text in (("viewer", VIEWER), ("editor", EDITOR), ("admin", ADMIN)):
        for line in text.strip().splitlines():
            method, path = line.split()
            assert (method, path) not in out, f"listed twice: {method} {path}"
            out[(method, path)] = level
    return out


def _calls(dependant):  # noqa: ANN001
    for d in dependant.dependencies:
        yield d.call
        yield from _calls(d)


def _level(route, method: str) -> str | None:  # noqa: ANN001
    """The access level the route's dependencies actually enforce for this method."""
    calls = set(_calls(route.dependant))
    level = None
    if auth.require_user in calls:
        level = "viewer"
    if auth.require_content in calls:
        level = "editor" if method in UNSAFE else "viewer"
    if auth.require_editor in calls:
        level = "editor"
    if auth.require_admin in calls:
        level = "admin"
    return level


def _private(app):  # noqa: ANN001
    for r in _all_routes(app):
        if is_public(r.path) or r.is_ws:
            continue
        for method in sorted(r.methods - {"HEAD"}):
            yield r, method


def test_every_route_is_classified(app):
    table = _table()
    seen = set()
    wrong = []
    for r, method in _private(app):
        seen.add((method, r.path))
        want = table.get((method, r.path))
        got = _level(r, method)
        if want != got:
            wrong.append(f"{method} {r.path}: table says {want}, the route enforces {got}")
    for r in _all_routes(app):
        if r.is_ws and not is_public(r.path):
            assert r.path in WEBSOCKETS, f"unclassified websocket {r.path}"
    assert not wrong, "\n".join(wrong)
    assert not set(table) - seen, f"routes in the table that no longer exist: {sorted(set(table) - seen)}"


async def test_a_viewer_gets_403_where_an_editor_or_admin_is_needed(app, viewer):
    table = _table()
    for r, method in _private(app):
        if table[(method, r.path)] == "viewer":
            continue
        url = _fill(r.path)[0]
        res = await viewer.request(method, url)
        assert res.status_code == 403, f"{method} {url} -> {res.status_code} {res.text[:200]}"


async def test_an_editor_gets_403_where_an_admin_is_needed(app, editor):
    table = _table()
    for r, method in _private(app):
        if table[(method, r.path)] != "admin":
            continue
        url = _fill(r.path)[0]
        res = await editor.request(method, url)
        assert res.status_code == 403, f"{method} {url} -> {res.status_code} {res.text[:200]}"


async def test_a_viewer_reads(app, viewer, editor):
    """The routes of their level answer a viewer (or an editor) with something other than 403."""
    table = _table()
    for client, levels in ((viewer, {"viewer"}), (editor, {"viewer", "editor"})):
        for r, method in _private(app):
            if method != "GET" or table[(method, r.path)] not in levels or r.path.endswith("/events"):
                continue
            url = _fill(r.path)[0]
            res = await client.get(url)
            assert res.status_code != 403, f"GET {url} -> 403"
    assert (await viewer.get("/api/courses")).status_code == 200
    assert (await viewer.get("/api/account/passkeys")).status_code == 200
