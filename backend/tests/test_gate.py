"""Runtime tests of the AI gateway against a local OpenAI-compatible mock server:
role resolution, fallback models, the cost log, reply classification."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.db import SessionLocal
from app.egress import gate
from app.egress.schemas import EgressRequest, Message, Part
from app.models import AuditLog, Job

RECEIVED: list[dict] = []


async def _chat(request: Request):
    body = await request.json()
    RECEIVED.append({"path": request.url.path, "body": body, "auth": request.headers.get("authorization")})
    if body.get("model") == "broken-model":
        return JSONResponse({"error": {"message": "upstream exploded"}}, status_code=500)
    if body.get("model") == "short-model":
        return JSONResponse({"choices": [{"message": {"content": "half a repl"}, "finish_reason": "length"}],
                             "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    content = json.dumps({"ok": True, "echo": body["messages"][-1]["content"] if isinstance(body["messages"][-1]["content"], str) else "parts"})
    return JSONResponse({"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


async def _models(request: Request):
    RECEIVED.append({"path": request.url.path, "body": None, "auth": request.headers.get("authorization")})
    return JSONResponse({"data": [{"id": "mock-model"}]})


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def mock_provider_url():
    port = _free_port()
    app = Starlette(routes=[Route("/v1/chat/completions", _chat, methods=["POST"]), Route("/v1/models", _models)])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True


@pytest.fixture(autouse=True)
def _clear():
    RECEIVED.clear()


async def _provider(admin, url, *, name="Mock cloud", models=("mock-model",), enabled=True):
    r = await admin.post(
        "/api/providers",
        json={
            "name": name, "type": "openai_compat", "base_url": url, "api_key": "sk-test-123456789", "enabled": enabled,
            "models": [{"id": m, "input_per_mtok": 2.0, "output_per_mtok": 10.0} for m in models],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _req(key: str, text: str = "lecture notes", provider_id=None, model="mock-model", json_output=True, role="writing") -> EgressRequest:
    return EgressRequest(
        role=role, task="test.echo", request_key=key, system="You are a test.", json_output=json_output,
        provider_id=provider_id, model=model if provider_id else None,
        messages=[Message(role="user", parts=[Part(type="text", text=text)])],
    )


async def test_send_and_cost_log(admin, mock_provider_url, db):
    p = await _provider(admin, mock_provider_url)
    assert p["usable"] and "zdr" not in p
    job = Job(kind="demo.sleep", title="costs", payload={})
    db.add(job)
    await db.commit()
    res = await gate.send(_req("k-send", provider_id=p["id"]), gate.GateContext(job_id=job.id))
    assert res.data["ok"] and len(RECEIVED) == 1 and RECEIVED[0]["auth"] == "Bearer sk-test-123456789"
    async with SessionLocal() as s:
        row = await s.get(AuditLog, res.audit_id)
        assert row.status == "ok" and row.tokens_in == 100 and row.tokens_out == 20
        assert abs(row.cost_usd - (100 * 2 + 20 * 10) / 1e6) < 1e-12
        j = await s.get(Job, job.id)
        assert j.tokens_in == 100 and abs(j.cost_usd - row.cost_usd) < 1e-12
    rows = (await admin.get("/api/audit")).json()
    assert rows and not any(k in rows[0] for k in ("payload_sha256", "has_copy", "authorization"))
    one = (await admin.get(f"/api/audit/{res.audit_id}")).json()
    assert one["task"] == "test.echo" and "copy" not in one
    costs = (await admin.get("/api/costs")).json()
    assert costs["months"] and any(x["id"] == job.id for x in costs["jobs"])


async def test_disabled_provider_and_missing_role_are_refused(admin, mock_provider_url):
    p = await _provider(admin, mock_provider_url, name="Off", enabled=False)
    assert not p["usable"]
    with pytest.raises(gate.GateRefused):
        await gate.send(_req("k-off", provider_id=p["id"]), gate.GateContext())
    await admin.put("/api/settings/roles", json={"handwriting": {"primary": {"provider_id": None, "model": None}}})
    with pytest.raises(gate.GateRefused):
        await gate.send(_req("k-norole", role="handwriting"), gate.GateContext())
    assert RECEIVED == []


async def test_fallback_model_is_used_when_the_primary_fails(admin, mock_provider_url):
    p = await _provider(admin, mock_provider_url, name="Two models", models=("broken-model", "mock-model"))
    r = await admin.put("/api/settings/roles", json={"classification": {
        "primary": {"provider_id": p["id"], "model": "broken-model"}, "fallback": {"provider_id": p["id"], "model": "mock-model"}}})
    assert r.status_code == 200, r.text
    res = await gate.send(_req("k-fb", role="classification"), gate.GateContext())
    assert res.model == "mock-model" and [x["body"]["model"] for x in RECEIVED] == ["broken-model", "mock-model"]
    async with SessionLocal() as s:
        rows = (await s.execute(AuditLog.__table__.select().where(AuditLog.request_key == "k-fb").order_by(AuditLog.id))).all()
    assert [r.status for r in rows] == ["error", "ok"]


async def test_plain_text_reply_cut_off_is_reported(admin, mock_provider_url):
    p = await _provider(admin, mock_provider_url, name="Short", models=("short-model",))
    with pytest.raises(gate.GateSendError) as exc:
        await gate.send(_req("k-short", provider_id=p["id"], model="short-model", json_output=False), gate.GateContext())
    assert exc.value.kind == "truncated"


async def test_fake_provider_through_the_gate(admin):
    r = await admin.post("/api/providers", json={"name": "Fake", "type": "fake"})
    fake = r.json()
    assert fake["usable"] and fake["models"]
    assert (await admin.post(f"/api/providers/{fake['id']}/test")).json()["ok"]
    req = EgressRequest(
        role="vision", task="read.pages", request_key="k-fake", system="s", json_output=False,
        provider_id=fake["id"], model="fake-large",
        meta={"label": "a.pdf", "pages": [{"page": 1, "title": "Titolo", "text": "Testo [[IMG x1]]", "has_image": False}]},
        messages=[Message(role="user", parts=[Part(type="text", text="Testo")])],
    )
    res = await gate.send(req, gate.GateContext(chat_session_id=424242))
    assert "\\section{Titolo}" in res.text and "\\lectaimage[Figura]{x1}" in res.text and res.text.rstrip().endswith("%%END")


async def test_prompts_versioning(admin):
    prompts = (await admin.get("/api/prompts")).json()
    p = next(x for x in prompts if x["key"] == "read.pages")
    assert p["is_default"]
    r = await admin.put("/api/prompts/read.pages", json={"text": "custom prompt {nonce}", "note": "mine"})
    assert r.json()["version"] == 1
    await admin.put("/api/prompts/read.pages", json={"text": "custom prompt v2"})
    p = next(x for x in (await admin.get("/api/prompts")).json() if x["key"] == "read.pages")
    assert p["active_version"] == 2 and p["text"] == "custom prompt v2" and len(p["versions"]) == 2
    await admin.post("/api/prompts/read.pages/versions/1/activate")
    p = next(x for x in (await admin.get("/api/prompts")).json() if x["key"] == "read.pages")
    assert p["active_version"] == 1
    await admin.post("/api/prompts/read.pages/reset")
    p = next(x for x in (await admin.get("/api/prompts")).json() if x["key"] == "read.pages")
    assert p["is_default"] and p["text"] == p["default_text"]


async def test_api_key_is_encrypted_and_never_returned(admin, db):
    from app.models import Provider

    r = await admin.post("/api/providers", json={"name": "Keyed", "type": "anthropic", "api_key": "sk-ant-verysecretkey"})
    out = r.json()
    assert "sk-ant-verysecretkey" not in json.dumps(out) and out["has_api_key"] and out["api_key_hint"] == "…tkey"
    row = await db.get(Provider, out["id"])
    assert row.api_key_enc and "verysecret" not in row.api_key_enc
    listing = (await admin.get("/api/providers")).text
    assert "verysecretkey" not in listing


async def test_disabling_a_provider_unassigns_roles(admin, mock_provider_url):
    p = await _provider(admin, mock_provider_url, name="Mock unassign")
    await admin.put("/api/settings/roles", json={"chat": {"primary": {"provider_id": p["id"], "model": "mock-model"}}})
    await admin.patch(f"/api/providers/{p['id']}", json={"enabled": False})
    roles = (await admin.get("/api/settings/roles")).json()
    assert roles["chat"]["primary"]["provider_id"] is None
    await asyncio.sleep(0)
