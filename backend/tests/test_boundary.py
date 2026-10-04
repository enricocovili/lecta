"""The public/private boundary.

Sweeps every route of the application: anything that isn't explicitly public
must answer 404 to anonymous callers for every HTTP method, with or without a
body, with a forged session cookie, etc. Public routes must only expose
published data.
"""

from __future__ import annotations

import re

from fastapi.routing import APIRoute
from starlette.routing import WebSocketRoute

from app.security.guard import is_public

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
PARAM_RE = re.compile(r"\{([^}:]+)(:[^}]+)?\}")


def _fill(path: str) -> list[str]:
    """Concrete URLs for a route template (numeric and textual ids)."""
    return [PARAM_RE.sub("1", path), PARAM_RE.sub("abc", path), PARAM_RE.sub("999999", path)]


class _R:
    """A flattened route: full path + the underlying route object."""

    def __init__(self, path: str, route):  # noqa: ANN001
        self.path = path
        self.route = route
        self.methods = getattr(route, "methods", None)
        self.dependant = getattr(route, "dependant", None)
        self.is_ws = isinstance(route, WebSocketRoute)


def _all_routes(app) -> list[_R]:
    """Effective (flattened) routes with their full paths, including included routers."""
    out: list[_R] = []
    try:  # FastAPI >= 0.140 keeps included routers as lazy wrappers
        from fastapi.routing import _iter_routes_with_context

        for route, ctx in _iter_routes_with_context(app.routes):
            if isinstance(route, APIRoute | WebSocketRoute):
                path = route.path
                if ctx is not None:
                    path = ctx.path or getattr(ctx.starlette_route, "path", None) or route.path
                out.append(_R(path, route))
    except ImportError:  # pragma: no cover
        out = [_R(r.path, r) for r in app.routes if isinstance(r, APIRoute | WebSocketRoute)]
    assert len(out) > 20 and all(r.path.startswith("/api") for r in out), [r.path for r in out][:5]
    return out


def test_every_private_route_is_guarded(app):
    """Static check: every non-public API route depends on require_admin (defence in depth)."""
    from app.security.auth import require_admin

    def has_admin_dep(dependant) -> bool:  # noqa: ANN001
        return any(d.call is require_admin or has_admin_dep(d) for d in dependant.dependencies)

    missing = []
    for r in _all_routes(app):
        if not r.is_ws and not is_public(r.path) and not has_admin_dep(r.dependant):
            missing.append(r.path)
    assert not missing, f"private routes without require_admin: {missing}"


async def test_anonymous_sweep_returns_404(app, anon, monkeypatch):
    from app.security.guard import NOT_FOUND_LIMIT

    monkeypatch.setattr(NOT_FOUND_LIMIT, "limit", 10**9)
    checked = 0
    for route in _all_routes(app):
        if is_public(route.path):
            continue
        for url in _fill(route.path):
            for method in METHODS:
                for kwargs in ({}, {"json": {"x": 1}}, {"content": b"not json", "headers": {"content-type": "application/json"}}):
                    r = await anon.request(method, url, **kwargs)
                    assert r.status_code == 404, f"{method} {url} -> {r.status_code} {r.text[:200]}"
                    if method != "HEAD":
                        assert r.json() == {"detail": "Not Found"}
                    checked += 1
    assert checked > 200


async def test_forged_session_cookie_is_rejected(app, anon, monkeypatch):
    from app.security.guard import NOT_FOUND_LIMIT

    monkeypatch.setattr(NOT_FOUND_LIMIT, "limit", 10**9)
    anon.cookies.set("lecta_session", "forged-token-value")
    for route in _all_routes(app):
        if is_public(route.path) or route.is_ws:
            continue
        url = _fill(route.path)[0]
        for method in route.methods or ["GET"]:
            r = await anon.request(method, url)
            assert r.status_code == 404, f"{method} {url}"


async def test_unknown_paths_404(anon):
    for url in ["/api/nope", "/api/", "/api", "/api/admin", "/docs", "/openapi.json", "/api/openapi.json", "/api/docs"]:
        r = await anon.get(url)
        assert r.status_code == 404, url


async def test_private_websocket_rejected(app):
    """Raw ASGI websocket handshake without a session is closed with 4404."""
    sent = []
    inbox = [{"type": "websocket.connect"}]

    async def receive():
        return inbox.pop(0) if inbox else {"type": "websocket.disconnect", "code": 1000}

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "websocket", "path": "/api/diag/ws", "raw_path": b"/api/diag/ws", "headers": [],
        "query_string": b"", "scheme": "ws", "server": ("test", 80), "client": ("1.2.3.4", 1), "subprotocols": [],
        "asgi": {"version": "3.0"},
    }
    await app(scope, receive, send)
    assert sent and sent[0]["type"] == "websocket.close" and sent[0]["code"] == 4404


async def test_public_routes_expose_only_published(admin, anon):
    r = await admin.post("/api/courses", json={"name": "Segreto", "chapters": ["Intro"]})
    assert r.status_code == 201
    slug = r.json()["slug"]
    assert slug not in [c["slug"] for c in (await anon.get("/api/public/courses")).json()]
    for url in [
        f"/api/public/courses/{slug}",
        f"/api/public/courses/{slug}.pdf",
        f"/api/public/courses/{slug}/chapters/intro.pdf",
    ]:
        assert (await anon.get(url)).status_code == 404, url
    assert (await anon.get("/api/public/search?q=Segreto")).json() == []
    # Public site info carries no private data.
    site = (await anon.get("/api/public/site")).json()
    assert set(site) == {"title", "description", "byline", "allow_indexing"}


async def test_private_responses_are_not_cacheable(admin):
    r = await admin.get("/api/courses")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_authenticated_websocket_echo(app, admin):
    """The guard lets an authenticated websocket through (raw ASGI, same event loop)."""
    import asyncio

    cookie = "; ".join(f"{k}={v}" for k, v in admin.cookies.items())
    sent = []
    inbox: asyncio.Queue = asyncio.Queue()
    await inbox.put({"type": "websocket.connect"})
    await inbox.put({"type": "websocket.receive", "text": "hi"})

    async def receive():
        if inbox.empty() and any(m["type"] == "websocket.send" for m in sent):
            return {"type": "websocket.disconnect", "code": 1000}
        return await asyncio.wait_for(inbox.get(), 5)

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "websocket", "path": "/api/diag/ws", "raw_path": b"/api/diag/ws",
        "headers": [(b"cookie", cookie.encode())], "query_string": b"", "scheme": "ws",
        "server": ("test", 80), "client": ("1.2.3.4", 1), "subprotocols": [], "asgi": {"version": "3.0"},
    }
    await app(scope, receive, send)
    assert {"type": "websocket.accept", "subprotocol": None, "headers": []} in sent or sent[0]["type"] == "websocket.accept"
    assert any(m.get("text") == "echo:hi" for m in sent), sent


async def test_anonymous_probing_is_rate_limited(anon):
    from app.security.guard import NOT_FOUND_LIMIT

    codes = [(await anon.get(f"/api/courses/{i}")).status_code for i in range(NOT_FOUND_LIMIT.limit + 5)]
    assert codes[0] == 404 and codes[-1] == 429
    # Every path answers the same way once blocked: nothing about existence leaks.
    assert (await anon.get("/api/this-does-not-exist")).status_code == 429
    NOT_FOUND_LIMIT.hits.clear()
