"""Diagnostics: provider replies classified precisely, structured job logs, grouped problems."""

from __future__ import annotations

import pytest

from app.db import SessionLocal
from app.egress import transport
from app.egress.adapters.base import Completion, Prepared, ProviderConfig
from app.egress.adapters.openai import OpenAICompatAdapter
from app.egress.gate import reply_problem
from app.models import AuditLog, Job, JobLog

CFG = ProviderConfig(id=1, name="OR", type="openai_compat", base_url="https://openrouter.ai/api/v1", api_key="k")
REQ = Prepared(model="m", system="s", messages=[("user", [])], max_tokens=6000, temperature=None, json_output=True, task="vision.page",
               request_key="k")


async def _complete_with(monkeypatch, body: dict) -> Completion:
    async def fake_request_json(*a, **kw):  # noqa: ANN002, ANN003
        return body

    monkeypatch.setattr(transport, "request_json", fake_request_json)
    return await OpenAICompatAdapter().complete(CFG, REQ)


async def test_openrouter_error_object_is_a_classified_error(monkeypatch):
    body = {"error": {"code": 429, "message": "Rate limit exceeded", "metadata": {"provider_name": "Luna", "raw": "slow down"}}}
    with pytest.raises(transport.ProviderReplyError) as e:
        await _complete_with(monkeypatch, body)
    assert e.value.kind == "rate_limit" and "Rate limit exceeded" in str(e.value) and "Luna" in str(e.value)


async def test_finish_reason_and_upstream_are_kept(monkeypatch):
    body = {"provider": "Luna", "choices": [{"finish_reason": "length", "message": {"content": '{"text": "cut'}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 6000, "completion_tokens_details": {"reasoning_tokens": 5800}}}
    c = await _complete_with(monkeypatch, body)
    assert c.finish_reason == "length" and c.info["upstream"] == "Luna" and c.info["reasoning_tokens"] == 5800
    p = reply_problem(c, 6000, True)
    assert p is not None and p.kind == "truncated" and "max_tokens=6000" in str(p)


def test_useless_replies_are_told_apart():
    assert reply_problem(Completion("", 0, 0), 100, True).kind == "empty"
    assert "did not run the model" in str(reply_problem(Completion("", 0, 0), 100, True))
    assert reply_problem(Completion("", 50, 100, finish_reason="length"), 100, True).kind == "truncated"
    assert reply_problem(Completion("", 50, 0, finish_reason="content_filter"), 100, True).kind == "refused"
    bad = reply_problem(Completion("Sure! Here is the page", 50, 10, finish_reason="stop"), 100, True)
    assert bad.kind == "invalid_json" and "Sure! Here is the page" in str(bad)
    assert reply_problem(Completion('{"ok": 1}', 5, 5, finish_reason="stop"), 100, True) is None
    assert reply_problem(Completion("plain text", 5, 5), 100, False) is None


async def test_problems_are_grouped_and_logs_filtered(admin):
    async with SessionLocal() as db:
        job = Job(kind="ingest", title="diag", status="failed", error="vision.page failed", progress_text="reading pages")
        db.add(job)
        await db.flush()
        for page in (1, 2, 3):
            a = AuditLog(provider_name="OR", provider_type="openai_compat", model="m", task="vision.page",
                         status="error", error="[empty] empty reply", job_id=job.id)
            db.add(a)
            await db.flush()
            db.add(JobLog(job_id=job.id, level="warn", message=f"Read deck.pdf · p. {page}: vision.page attempt 1/2 failed [empty]: x",
                          context={"stage": "vision", "kind": "empty", "item": f"Read deck.pdf · p. {page}", "audit_id": a.id}))
        db.add(JobLog(job_id=job.id, level="error", message="Figure f1x1 (deck.pdf · p. 2) [diagram]: could not be recreated",
                      context={"stage": "diagram", "kind": "figure_error", "figure": "f1x1"}))
        db.add(JobLog(job_id=job.id, level="info", message="manifest: 3 items", context={"stage": "analyze"}))
        await db.commit()
        job_id = job.id
    r = (await admin.get(f"/api/jobs/{job_id}/problems")).json()
    assert r["job"]["at"] == "reading pages"
    first = r["problems"][0]  # errors first
    assert first["kind"] == "figure_error" and first["count"] == 1
    empty = next(p for p in r["problems"] if p["kind"] == "empty")
    assert empty["count"] == 3 and len(empty["items"]) == 3 and empty["audit_ids"]
    assert r["calls"][0]["error"] == 3 and r["calls"][0]["error_kinds"] == {"empty": 3}
    logs = (await admin.get(f"/api/jobs/{job_id}/logs", params={"level": "warn", "stage": "vision"})).json()
    assert len(logs) == 3 and logs[0]["context"]["kind"] == "empty"
    assert len((await admin.get(f"/api/jobs/{job_id}/logs", params={"q": "p. 2"})).json()) == 2  # page read + its figure
    txt = await admin.get(f"/api/jobs/{job_id}/logs.txt")
    assert txt.status_code == 200 and "attachment" in txt.headers["content-disposition"] and "kind=empty" in txt.text
