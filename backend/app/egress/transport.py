"""The HTTP client used by the provider adapters (JSON requests and SSE streams)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


def error_kind(status: int | str | None) -> str:
    """Normalise an HTTP status / provider error code into a problem kind for the logs."""
    try:
        code = int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "provider_error"
    if code == 429:
        return "rate_limit"
    if code in (401, 403):
        return "auth"
    if code == 402:
        return "quota"
    if code in (408, 504, 524):
        return "timeout"
    if code >= 500:
        return "provider_error"
    return "bad_request"


class ProviderHTTPError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"provider returned HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body
        self.kind = error_kind(status)


class ProviderReplyError(RuntimeError):
    """The provider answered (often with HTTP 200) but with an error instead of a completion."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# Observers for tests: called with (method, url, json_body) right before a request.
_observers: list = []


def _client(timeout: float) -> httpx.AsyncClient:
    # No env proxies, no redirects: the request goes exactly to the configured provider.
    return httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False)


async def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout: float = 300,
) -> dict[str, Any]:
    for obs in list(_observers):
        obs(method, url, body)
    async with _client(timeout) as client:
        r = await client.request(method, url, headers=headers, json=body)
    if r.status_code >= 400:
        raise ProviderHTTPError(r.status_code, r.text)
    try:
        return r.json()
    except ValueError as e:
        raise ProviderHTTPError(r.status_code, "invalid JSON from provider") from e


async def stream_sse(
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float = 300,
) -> AsyncIterator[dict[str, Any]]:
    """POST and yield each `data:` JSON object of a server-sent-events response."""
    for obs in list(_observers):
        obs("POST", url, body)
    async with _client(timeout) as client:
        async with client.stream("POST", url, headers=headers, json=body) as r:
            if r.status_code >= 400:
                raise ProviderHTTPError(r.status_code, (await r.aread()).decode("utf-8", "replace"))
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    yield json.loads(data)
                except ValueError:
                    continue
