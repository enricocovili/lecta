"""Adapter interface. Adding a provider type = one module implementing `Adapter`
and one line in `adapters/__init__.py`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any



@dataclass
class ProviderConfig:
    id: int
    name: str
    type: str
    base_url: str
    api_key: str | None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreparedPart:
    type: str  # text | image | tool_call | tool_result
    text: str | None = None
    data_b64: str | None = None
    mime: str | None = None
    tool_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_extra: dict[str, Any] | None = None
    is_error: bool = False


@dataclass
class PreparedCall:
    id: str
    name: str
    args: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Prepared:
    model: str
    system: str
    messages: list[tuple[str, list[PreparedPart]]]
    max_tokens: int
    temperature: float | None
    json_output: bool
    task: str
    request_key: str
    meta: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)  # [{name, description, parameters}]


@dataclass
class Completion:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    # Normalised: stop | length | content_filter | refusal | other (None = not reported).
    finish_reason: str | None = None
    # Provider details worth logging (native finish reason, upstream provider, reasoning tokens, …).
    info: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[PreparedCall] = field(default_factory=list)


def norm_finish(reason: str | None) -> str | None:
    if not reason:
        return None
    r = str(reason).lower()
    if r in ("stop", "end_turn", "stop_sequence", "tool_calls", "tool_use"):
        return "stop"
    if r in ("length", "max_tokens", "max_output_tokens"):
        return "length"
    if r in ("content_filter", "safety", "recitation", "blocklist", "prohibited_content", "spii"):
        return "content_filter"
    if r == "refusal":
        return "refusal"
    return r[:40]


class Adapter:
    type: str = ""
    label: str = ""
    default_base_url: str = ""
    needs_key: bool = True

    async def complete(self, cfg: ProviderConfig, req: Prepared) -> Completion:
        raise NotImplementedError

    async def stream(self, cfg: ProviderConfig, req: Prepared) -> AsyncIterator[str | Completion]:
        """Yield text deltas, then one final Completion (with usage)."""
        c = await self.complete(cfg, req)
        yield c.text
        yield c

    async def list_models(self, cfg: ProviderConfig) -> list[dict[str, Any]]:
        return []

    def estimate_image_tokens(self, width: int, height: int) -> int:
        return max(85, (width * height) // 750)

    @staticmethod
    def url(cfg: ProviderConfig, path: str) -> str:
        return cfg.base_url.rstrip("/") + path
