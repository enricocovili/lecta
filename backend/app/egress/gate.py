"""The AI gateway: the only code that calls AI providers.

For every request:
1. resolve provider/model from the role assignment (or the caller's pin);
2. refuse disabled or unknown providers;
3. send it (bounded concurrency, timeout), falling back to the role's backup model on failure;
4. log the call (provider, model, task, tokens, cost, duration) for the cost overview.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import appsecrets
from ..db import SessionLocal
from ..models import AuditLog, Job, Provider
from ..services import blobs
from ..services import settings as settings_svc
from .adapters import ADAPTERS
from .adapters.base import Completion, Prepared, PreparedPart, ProviderConfig
from .schemas import EgressRequest, EgressResult, ToolCall, Usage

log = logging.getLogger("lecta.gate")


class GateRefused(Exception):
    """The request can't be sent at all (no provider/model assigned, provider disabled)."""


class GateSendError(Exception):
    """The provider call failed.

    `kind` classifies the problem for the job logs: rate_limit, quota, auth, timeout, network,
    provider_error, bad_request, empty, truncated, refused, invalid_json.
    """

    def __init__(self, message: str, *, kind: str = "provider_error", audit_id: int | None = None, provider: str | None = None,
                 model: str | None = None, info: dict[str, Any] | None = None):
        super().__init__(message)
        self.kind = kind
        self.audit_id = audit_id
        self.provider = provider
        self.model = model
        self.info = info or {}


def _exc_kind(e: BaseException) -> str:
    if isinstance(e, TimeoutError | asyncio.TimeoutError):
        return "timeout"
    kind = getattr(e, "kind", None)
    if isinstance(kind, str):
        return kind
    if e.__class__.__module__.startswith(("httpx", "httpcore")):
        return "network"
    return "provider_error"


def _describe_reply(c: Completion, max_tokens: int) -> str:
    bits = [f"finish_reason={c.info.get('finish_reason') or c.finish_reason or 'none'}"]
    for k in ("native_finish_reason", "upstream", "reasoning_tokens"):
        if c.info.get(k):
            bits.append(f"{k}={c.info[k]}")
    bits.append(f"tokens in/out={c.tokens_in}/{c.tokens_out}")
    if c.finish_reason == "length":
        bits.append(f"max_tokens={max_tokens}")
    return ", ".join(bits)


def reply_problem(c: Completion, max_tokens: int, json_output: bool) -> GateSendError | None:
    """A useless reply (empty / refused / cut off / not JSON) as a classified error, else None."""
    details = _describe_reply(c, max_tokens)
    if c.info.get("refusal"):
        return GateSendError(f"the model refused: {str(c.info['refusal'])[:300]} ({details})", kind="refused", info=c.info)
    if not c.text.strip() and not c.tool_calls:
        if c.finish_reason == "length":
            msg = "empty reply: the output budget ran out before any text (reasoning?)"
            return GateSendError(f"{msg} ({details})", kind="truncated", info=c.info)
        if c.finish_reason in ("content_filter", "refusal"):
            return GateSendError(f"empty reply: blocked by the provider's content filter ({details})", kind="refused", info=c.info)
        if not c.tokens_in and not c.tokens_out:
            msg = "empty reply without any token usage: the provider did not run the model (rate limit or upstream error?)"
        else:
            msg = "empty reply from the model"
        return GateSendError(f"{msg} ({details})", kind="empty", info=c.info)
    if json_output:
        try:
            parse_json(c.text)
        except GateSendError:
            if c.finish_reason == "length":
                return GateSendError(f"reply cut off at the output limit before the JSON was complete ({details})", kind="truncated", info=c.info)
            head = " ".join(c.text.strip()[:160].split())
            return GateSendError(f"reply is not valid JSON; it starts with: {head!r} ({details})", kind="invalid_json", info=c.info)
    elif c.finish_reason == "length":
        return GateSendError(f"reply cut off at the output limit ({details})", kind="truncated", info=c.info)
    return None


@dataclass
class GateContext:
    job_id: int | None = None
    chat_session_id: int | None = None
    course_id: int | None = None
    title: str = ""


_sem: asyncio.Semaphore | None = None
_sem_size = 0


async def _semaphore(db: AsyncSession) -> asyncio.Semaphore:
    global _sem, _sem_size
    size = (await settings_svc.get_section(db, "ai")).max_concurrent_requests
    if _sem is None or size != _sem_size:
        _sem, _sem_size = asyncio.Semaphore(size), size
    return _sem


# --------------------------------------------------------------------------- providers & roles


def provider_allowed(p: Provider) -> str | None:
    """Why this provider may not be used, or None."""
    if not p.enabled:
        return "provider is disabled"
    if p.type not in ADAPTERS:
        return f"unknown provider type {p.type!r}"
    return None


async def resolve_role(db: AsyncSession, role: str) -> tuple[Provider, str, Provider | None, str | None]:
    roles = await settings_svc.get_section(db, "roles")
    assignment = getattr(roles, role, None)
    if assignment is None or not assignment.primary.provider_id or not assignment.primary.model:
        raise GateRefused(f"no model assigned to the “{settings_svc.ROLE_LABELS.get(role, role)}” role (Settings → Models)")
    p = await db.get(Provider, assignment.primary.provider_id)
    if p is None:
        raise GateRefused(f"the provider assigned to role {role} no longer exists")
    fb, fbm = None, None
    if assignment.fallback.provider_id and assignment.fallback.model:
        fb = await db.get(Provider, assignment.fallback.provider_id)
        fbm = assignment.fallback.model
        if fb is not None and provider_allowed(fb):
            fb, fbm = None, None
    return p, assignment.primary.model, fb, fbm


async def validate_role_settings(db: AsyncSession, value: dict[str, Any]) -> None:
    """Role assignments may only point at existing, enabled providers."""
    from fastapi import HTTPException

    for role, assignment in (value or {}).items():
        if role not in settings_svc.ROLES or not isinstance(assignment, dict):
            continue
        for slot in ("primary", "fallback"):
            target = assignment.get(slot) or {}
            pid = target.get("provider_id")
            if not pid:
                continue
            p = await db.get(Provider, int(pid))
            if p is None:
                raise HTTPException(status_code=422, detail=f"{role}.{slot}: provider not found")
            why = provider_allowed(p)
            if why:
                raise HTTPException(status_code=422, detail=f"{role}.{slot}: {p.name}: {why}")


def _config(p: Provider) -> ProviderConfig:
    adapter = ADAPTERS[p.type]
    return ProviderConfig(
        id=p.id,
        name=p.name,
        type=p.type,
        base_url=(p.base_url or adapter.default_base_url).rstrip("/"),
        api_key=appsecrets.decrypt(p.api_key_enc),
        options=p.options or {},
    )


def _price(p: Provider, model: str) -> tuple[float, float]:
    for m in p.models or []:
        if m.get("id") == model:
            return float(m.get("input_per_mtok") or 0), float(m.get("output_per_mtok") or 0)
    return 0.0, 0.0


def cost_of(p: Provider, model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = _price(p, model)
    return tokens_in / 1e6 * pin + tokens_out / 1e6 * pout


def provider_types() -> list[dict[str, Any]]:
    return [
        {"type": a.type, "label": a.label, "default_base_url": a.default_base_url, "needs_key": a.needs_key}
        for a in ADAPTERS.values()
    ]


def provider_type_info(t: str) -> dict[str, Any] | None:
    return next((x for x in provider_types() if x["type"] == t), None)


def new_nonce() -> str:
    return secrets.token_hex(6)


# --------------------------------------------------------------------------- sending


def _prepare(req: EgressRequest, model: str) -> Prepared:
    msgs: list[tuple[str, list[PreparedPart]]] = []
    for m in req.messages:
        parts = []
        for part in m.parts:
            if part.type == "text":
                parts.append(PreparedPart("text", text=part.text or ""))
            elif part.type == "tool_call":
                parts.append(PreparedPart("tool_call", tool_id=part.tool_id, tool_name=part.tool_name, tool_args=part.tool_args or {},
                                          tool_extra=part.tool_extra or {}))
            elif part.type == "tool_result":
                parts.append(PreparedPart("tool_result", text=part.text or "", tool_id=part.tool_id, tool_name=part.tool_name,
                                          is_error=bool(part.is_error)))
            else:
                data = blobs.read_bytes(part.blob or "")
                parts.append(PreparedPart("image", data_b64=base64.b64encode(data).decode(), mime=part.mime or "image/png"))
        msgs.append((m.role, parts))
    return Prepared(
        model=model,
        system=req.system,
        messages=msgs,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        json_output=req.json_output,
        task=req.task,
        request_key=req.request_key,
        meta=req.meta,
        tools=[t.model_dump() for t in req.tools],
    )


def parse_json(text: str) -> Any:
    """Parse a model's JSON reply, tolerating code fences and surrounding prose."""
    t = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if fence:
        t = fence.group(1)
    try:
        return json.loads(t)
    except ValueError:
        pass
    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(t[start : end + 1])
        except ValueError:
            pass
    raise GateSendError("the model did not return valid JSON")


async def _begin_audit(db: AsyncSession, req: EgressRequest, p: Provider, model: str, ctx: GateContext) -> AuditLog:
    row = AuditLog(
        provider_id=p.id,
        provider_name=p.name,
        provider_type=p.type,
        model=model,
        role=req.role,
        task=req.task,
        request_key=req.request_key,
        status="sending",
        job_id=ctx.job_id,
        chat_session_id=ctx.chat_session_id,
    )
    db.add(row)
    await db.commit()
    return row


async def _finish_audit(
    audit_id: int, req: EgressRequest, *, status: str, text: str | None, usage: Usage, cost: float, ms: int, error: str | None,
) -> None:
    async with SessionLocal() as db:
        row = await db.get(AuditLog, audit_id)
        if row is None:
            return
        row.status = status
        row.error = error
        row.tokens_in = usage.tokens_in
        row.tokens_out = usage.tokens_out
        row.cost_usd = cost
        row.duration_ms = ms
        if row.job_id is not None and status == "ok":
            job = await db.get(Job, row.job_id)
            if job is not None:
                job.tokens_in += usage.tokens_in
                job.tokens_out += usage.tokens_out
                job.cost_usd += cost
        await db.commit()


async def _load(db: AsyncSession, req: EgressRequest) -> tuple[Provider, str, Provider | None, str | None]:
    if req.provider_id:
        p = await db.get(Provider, req.provider_id)
        if p is None:
            raise GateRefused("provider not found")
        return p, req.model or "", None, None
    return await resolve_role(db, req.role)


async def send(req: EgressRequest, ctx: GateContext) -> EgressResult:
    """Send a request (raises GateRefused / GateSendError)."""
    async with SessionLocal() as db:
        p, model, fb, fbm = await _load(db, req)
        why = provider_allowed(p)
        if why:
            raise GateRefused(f"{p.name}: {why}")
        req = req.model_copy(update={"provider_id": p.id, "model": model})
        sem = await _semaphore(db)
        timeout = (await settings_svc.get_section(db, "ai")).request_timeout_s
    try:
        return await _dispatch(req, p, model, ctx, sem, timeout)
    except GateSendError:
        if fb is None:
            raise
        log.warning("primary provider failed for %s; using fallback %s", req.request_key, fb.name)
        fb_req = req.model_copy(update={"provider_id": fb.id, "model": fbm})
        return await _dispatch(fb_req, fb, fbm or "", ctx, sem, timeout)


async def _dispatch(req: EgressRequest, p: Provider, model: str, ctx: GateContext, sem: asyncio.Semaphore, timeout: float) -> EgressResult:
    adapter = ADAPTERS[p.type]
    cfg = _config(p)
    prepared = _prepare(req, model)
    async with SessionLocal() as db:
        audit = await _begin_audit(db, req, p, model, ctx)
        audit_id = audit.id
    t0 = time.monotonic()
    async with sem:
        try:
            c: Completion = await asyncio.wait_for(adapter.complete(cfg, prepared), timeout=timeout)
        except Exception as e:
            ms = int((time.monotonic() - t0) * 1000)
            kind = _exc_kind(e)
            msg = f"timed out after {timeout:.0f}s" if kind == "timeout" and not str(e) else str(e)
            await _finish_audit(audit_id, req, status="error", text=None, usage=Usage(), cost=0.0, ms=ms, error=f"[{kind}] {msg}"[:2000])
            raise GateSendError(f"{p.name}/{model}: {msg}", kind=kind, audit_id=audit_id, provider=p.name, model=model) from e
    usage = Usage(tokens_in=c.tokens_in, tokens_out=c.tokens_out)
    cost = cost_of(p, model, c.tokens_in, c.tokens_out)
    problem = reply_problem(c, req.max_tokens, req.json_output)
    if problem is not None:
        await _finish_audit(audit_id, req, status="error", text=c.text, usage=usage, cost=cost,
                            ms=int((time.monotonic() - t0) * 1000), error=f"[{problem.kind}] {problem}"[:2000])
        problem.audit_id, problem.provider, problem.model = audit_id, p.name, model
        raise problem
    data = parse_json(c.text) if req.json_output else None
    await _finish_audit(audit_id, req, status="ok", text=c.text, usage=usage, cost=cost, ms=int((time.monotonic() - t0) * 1000), error=None)
    return EgressResult(
        text=c.text, data=data, usage=usage, cost_usd=cost, provider_id=p.id, provider_name=p.name, model=model,
        request_key=req.request_key, audit_id=audit_id, finish_reason=c.finish_reason,
        tool_calls=[ToolCall(id=t.id, name=t.name, args=t.args, extra=t.extra) for t in c.tool_calls],
    )


async def stream(req: EgressRequest, ctx: GateContext) -> AsyncIterator[str | EgressResult]:
    """Like send(), but yields text deltas and finally the EgressResult.
    Provider problems that can be detected before sending (GateRefused) are raised right away."""
    async with SessionLocal() as db:
        p, model, _, _ = await _load(db, req)
        why = provider_allowed(p)
        if why:
            raise GateRefused(f"{p.name}: {why}")
        req = req.model_copy(update={"provider_id": p.id, "model": model})
        sem = await _semaphore(db)
        timeout = (await settings_svc.get_section(db, "ai")).request_timeout_s
        audit = await _begin_audit(db, req, p, model, ctx)
        audit_id = audit.id
    return _stream_gen(req, p, model, audit_id, sem, timeout)


async def _stream_gen(req: EgressRequest, p: Provider, model: str, audit_id: int, sem: asyncio.Semaphore, timeout: float):
    adapter = ADAPTERS[p.type]
    cfg = _config(p)
    prepared = _prepare(req, model)
    t0 = time.monotonic()
    final: Completion | None = None
    text = ""
    async with sem:
        try:
            async with asyncio.timeout(timeout):
                async for chunk in adapter.stream(cfg, prepared):
                    if isinstance(chunk, Completion):
                        final = chunk
                    else:
                        text += chunk
                        yield chunk
        except Exception as e:
            kind = _exc_kind(e)
            await _finish_audit(audit_id, req, status="error", text=text or None, usage=Usage(), cost=0.0,
                                ms=int((time.monotonic() - t0) * 1000), error=f"[{kind}] {e}"[:2000])
            raise GateSendError(f"{p.name}/{model}: {e}", kind=kind, audit_id=audit_id, provider=p.name, model=model) from e
    final = final or Completion(text)
    usage = Usage(tokens_in=final.tokens_in, tokens_out=final.tokens_out)
    cost = cost_of(p, model, usage.tokens_in, usage.tokens_out)
    await _finish_audit(audit_id, req, status="ok", text=final.text, usage=usage, cost=cost, ms=int((time.monotonic() - t0) * 1000), error=None)
    yield EgressResult(text=final.text, usage=usage, cost_usd=cost, provider_id=p.id, provider_name=p.name, model=model,
                       request_key=req.request_key, audit_id=audit_id, finish_reason=final.finish_reason,
                       tool_calls=[ToolCall(id=t.id, name=t.name, args=t.args, extra=t.extra) for t in final.tool_calls])


# --------------------------------------------------------------------------- provider maintenance calls


async def _maintenance_call(provider_id: int, purpose: str):
    async with SessionLocal() as db:
        p = await db.get(Provider, provider_id)
        if p is None:
            raise GateRefused("provider not found")
        if p.type not in ADAPTERS:
            raise GateRefused("unknown provider type")
        row = AuditLog(provider_id=p.id, provider_name=p.name, provider_type=p.type, model="-", task=purpose, status="sending")
        db.add(row)
        await db.commit()
        return p, row.id


async def test_provider(provider_id: int, model: str | None = None) -> dict[str, Any]:
    """Connection test: list models (sends only the API key, no content)."""
    p, audit_id = await _maintenance_call(provider_id, "provider.test")
    adapter = ADAPTERS[p.type]
    cfg = _config(p)
    t0 = time.monotonic()
    ok, detail, models = False, "", []
    try:
        models = await asyncio.wait_for(adapter.list_models(cfg), 30)
        ok, detail = True, f"{len(models)} models available"
    except Exception as e:  # noqa: BLE001
        detail = str(e)[:500]
    ms = int((time.monotonic() - t0) * 1000)
    async with SessionLocal() as db:
        row = await db.get(AuditLog, audit_id)
        if row:
            row.status, row.error, row.duration_ms = ("ok" if ok else "error"), (None if ok else detail), ms
        prov = await db.get(Provider, provider_id)
        if prov:
            prov.last_test = {"ok": ok, "detail": detail, "at": datetime.now(UTC).isoformat(), "ms": ms}
        await db.commit()
    return {"ok": ok, "detail": detail, "ms": ms, "models": models}
