"""Client for the compile service (HTTP over the unix socket shared with the
`latex` container). This is not an AI provider: it never leaves the host."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from ..config import config


class CompileServiceError(RuntimeError):
    pass


def rel(path: Path) -> str:
    """Path relative to the shared latex volume, as the compile service expects."""
    return str(path.resolve().relative_to(config.latex_root.resolve()))


async def _post(endpoint: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    transport = httpx.AsyncHTTPTransport(uds=config.latex_socket)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://latex", timeout=timeout) as client:
            r = await client.post(endpoint, json=body)
    except httpx.HTTPError as e:
        raise CompileServiceError(f"compile service unavailable: {e}") from e
    if r.status_code != 200:
        raise CompileServiceError(f"compile service error {r.status_code}: {r.text[:500]}")
    return r.json()


async def compile(body: dict[str, Any]) -> dict[str, Any]:
    return await _post("/compile", body, float(body.get("timeout", 120)) * 3 + 60)


async def figure(body: dict[str, Any]) -> dict[str, Any]:
    return await _post("/figure", body, float(body.get("timeout", 60)) + 30)


async def blocks(body: dict[str, Any]) -> dict[str, Any]:
    return await _post("/blocks", body, float(body.get("timeout", 120)) * 3 + 60)


async def synctex(body: dict[str, Any]) -> dict[str, Any]:
    return await _post("/synctex", body, 30)


async def health() -> bool:
    transport = httpx.AsyncHTTPTransport(uds=config.latex_socket)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://latex", timeout=5) as client:
            return (await client.get("/health")).status_code == 200
    except httpx.HTTPError:
        return False
