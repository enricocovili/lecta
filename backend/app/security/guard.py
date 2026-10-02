"""ASGI guard for the public/private boundary.

Every `/api/*` path that is not explicitly public requires a valid admin
session *before routing*. Anonymous callers get the same 404 as an unknown
URL, whatever the method, body or content type (so 405/422 can't leak which
private routes exist). Route-level `require_admin` dependencies are a second,
independent layer.
"""

from __future__ import annotations

import json

from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from ..db import SessionLocal
from .auth import RateLimiter, load_session

# Anonymous traffic (public pages, PDF downloads, probes of private URLs) is rate
# limited per client IP; the single admin's authenticated requests are not.
anon_limiter = RateLimiter(limit=300, window_s=60)
NOT_FOUND_LIMIT = RateLimiter(limit=60, window_s=60)

PUBLIC_EXACT = {
    "/api/health",
    "/api/setup/status",
    "/api/setup",
    "/api/auth/csrf",
    "/api/auth/login",
    "/api/auth/passkey/options",
    "/api/auth/passkey/login",
    "/api/auth/logout",
    "/api/auth/me",
}
PUBLIC_PREFIXES = ("/api/public/",)


def is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)


NOT_FOUND_BODY = json.dumps({"detail": "Not Found"}).encode()


class PrivateGuard:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        if not path.startswith("/api/") and path != "/api":
            # The backend only serves /api; anything else is unknown.
            await self._not_found(scope, receive, send)
            return
        request = HTTPConnection(scope)
        ip = request.client.host if request.client else "unknown"
        if not is_public(path):
            async with SessionLocal() as db:
                ctx = await load_session(request, db)
            if ctx is None:
                NOT_FOUND_LIMIT.hit(ip)
                if NOT_FOUND_LIMIT.blocked(ip):
                    await self._too_many(scope, receive, send)
                    return
                await self._not_found(scope, receive, send)
                return
            scope.setdefault("state", {})["auth"] = ctx
        elif path != "/api/health" and "lecta_session" not in request.cookies:
            anon_limiter.hit(ip)
            if anon_limiter.blocked(ip):
                await self._too_many(scope, receive, send)
                return
        await self.app(scope, receive, send)

    async def _too_many(self, scope: Scope, receive: Receive, send: Send) -> None:
        body = b'{"detail":"Too many requests"}'
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.close", "code": 4429})
            return
        await send({"type": "http.response.start", "status": 429,
                    "headers": [(b"content-type", b"application/json"), (b"retry-after", b"60"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def _not_found(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 4404})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 404,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(NOT_FOUND_BODY)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": NOT_FOUND_BODY})


class SecurityHeaders:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        public = is_public(scope.get("path", ""))

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"same-origin"))
                headers.append((b"x-frame-options", b"SAMEORIGIN"))
                if not public and b"cache-control" not in names:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
