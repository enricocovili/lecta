"""End-to-end proxy checks through the frontend (run by smoke.sh --full)."""

import asyncio
import hashlib
import os
import sys
import time

import httpx
import websockets

BASE = os.environ["BASE"]
CODE = os.environ["SETUP_CODE"]
PASSWORD = "smoke-test-password-123"
UPLOAD_MB = int(os.environ.get("UPLOAD_MB", "512"))


def ok(msg: str) -> None:
    print(f"ok   {msg}", flush=True)


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=120, follow_redirects=False) as c:
        r = await c.get("/")
        assert r.status_code == 302 and r.headers["location"].endswith("/setup"), r.status_code
        await c.get("/api/auth/csrf")
        csrf = c.cookies["lecta_csrf"]
        r = await c.post(
            "/api/setup",
            json={"setup_code": CODE, "username": "smoke", "password": PASSWORD},
            headers={"x-csrf-token": csrf, "origin": BASE},
        )
        assert r.status_code == 200, r.text
        me = (await c.get("/api/auth/me")).json()
        assert me["authenticated"]
        h = {"x-csrf-token": me["csrf"], "origin": BASE}
        ok("setup wizard + session through the proxy")

        for page in ["/admin", "/admin/courses", "/admin/jobs", "/admin/settings"]:
            r = await c.get(page)
            assert r.status_code == 200, (page, r.status_code)
        r = await c.post("/api/courses", json={"name": "Smoke course", "chapters": ["One"]}, headers=h)
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        assert (await c.get(f"/admin/courses/{cid}")).status_code == 200
        ok("admin pages render")

        # SSE: events must arrive incrementally, not all at the end.
        stamps = []
        async with c.stream("GET", "/api/diag/sse?n=4") as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    stamps.append(time.monotonic())
        assert len(stamps) == 4 and stamps[-1] - stamps[0] > 0.6, stamps
        ok("SSE streams incrementally")

        # WebSocket through the proxy, with the session cookie.
        ws_url = BASE.replace("http", "ws") + "/api/diag/ws"
        cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
        async with websockets.connect(ws_url, additional_headers={"cookie": cookie}) as ws:
            await ws.send("ping")
            assert await ws.recv() == "echo:ping"
        ok("WebSocket upgrade proxied")
        try:
            async with websockets.connect(ws_url) as ws:
                await ws.send("x")
                await ws.recv()
            raise AssertionError("anonymous websocket was accepted")
        except websockets.exceptions.ConnectionClosed as e:
            assert e.rcvd is not None and e.rcvd.code == 4404
        except websockets.exceptions.InvalidStatus:
            pass
        ok("anonymous WebSocket refused")

        # Large streaming upload (generated on the fly, never held in memory).
        digest = hashlib.sha256()
        chunk = os.urandom(1024 * 1024)

        async def body():
            for _ in range(UPLOAD_MB):
                digest.update(chunk)
                yield chunk

        t0 = time.monotonic()
        r = await c.post("/api/diag/upload", content=body(), headers={**h, "content-type": "application/octet-stream"}, timeout=900)
        assert r.status_code == 200, r.text
        assert r.json()["size"] == UPLOAD_MB * 1024 * 1024 and r.json()["sha256"] == digest.hexdigest()
        ok(f"{UPLOAD_MB} MB streaming upload in {time.monotonic() - t0:.1f}s, checksum matches")


try:
    asyncio.run(main())
except AssertionError as e:
    print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)
