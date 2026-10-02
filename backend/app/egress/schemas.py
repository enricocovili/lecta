"""Data shapes of the requests sent to AI providers."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

ROLES = ("vision", "handwriting", "writing", "classification", "chat")


class ToolSpec(BaseModel):
    """A function the model may call (JSON-schema parameters)."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    # Provider-specific data that must be echoed back with the call (e.g. Gemini thought signatures).
    extra: dict[str, Any] = Field(default_factory=dict)


class Part(BaseModel):
    # tool_call: an assistant turn asks for a function; tool_result: the answer (a user-side part).
    type: Literal["text", "image", "tool_call", "tool_result"]
    text: str | None = None
    tool_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_extra: dict[str, Any] | None = None
    is_error: bool | None = None
    blob: str | None = None  # sha256 of the image in the blob store
    mime: str | None = None
    width: int | None = None
    height: int | None = None
    label: str | None = None  # shown in the job logs, e.g. "slides.pdf · page 3"


class Message(BaseModel):
    role: Literal["user", "assistant"]
    parts: list[Part]


class EgressRequest(BaseModel):
    role: str
    task: str
    request_key: str
    system: str
    messages: list[Message]
    max_tokens: int = 4000
    temperature: float | None = 0.2
    json_output: bool = False
    tools: list[ToolSpec] = Field(default_factory=list)
    # Local-only metadata (never sent to real providers; the fake provider uses it).
    meta: dict[str, Any] = Field(default_factory=dict)
    # Resolved by the gate (or pinned by the caller).
    provider_id: int | None = None
    model: str | None = None

    def all_parts(self) -> list[Part]:
        return [p for m in self.messages for p in m.parts]

    def sent_view(self) -> dict[str, Any]:
        """Exactly what goes to the provider (no local-only meta)."""
        return {
            "provider_id": self.provider_id,
            "model": self.model,
            "system": self.system,
            "messages": [m.model_dump(exclude_none=True) for m in self.messages],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "json_output": self.json_output,
            "tools": [t.model_dump() for t in self.tools],
        }

    def payload_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.sent_view(), sort_keys=True, default=str).encode()).hexdigest()


class Usage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0


class EgressResult(BaseModel):
    text: str
    data: Any = None  # parsed JSON when json_output
    usage: Usage = Usage()
    cost_usd: float = 0.0
    provider_id: int
    provider_name: str
    model: str
    request_key: str
    audit_id: int | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
