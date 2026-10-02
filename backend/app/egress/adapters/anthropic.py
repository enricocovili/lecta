"""Anthropic Messages API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from .. import transport
from .base import Adapter, Completion, Prepared, PreparedCall, ProviderConfig, norm_finish

API_VERSION = "2023-06-01"


class AnthropicAdapter(Adapter):
    type = "anthropic"
    label = "Anthropic"
    default_base_url = "https://api.anthropic.com"

    def _headers(self, cfg: ProviderConfig) -> dict[str, str]:
        return {"x-api-key": cfg.api_key or "", "anthropic-version": API_VERSION, "content-type": "application/json"}

    def _body(self, req: Prepared) -> dict[str, Any]:
        messages = []
        for role, parts in req.messages:
            content = []
            # tool_result blocks must come first in a user message.
            for p in sorted(parts, key=lambda x: 0 if x.type == "tool_result" else 1):
                if p.type == "text":
                    if p.text:
                        content.append({"type": "text", "text": p.text})
                elif p.type == "tool_call":
                    content.append({"type": "tool_use", "id": p.tool_id, "name": p.tool_name, "input": p.tool_args or {}})
                elif p.type == "tool_result":
                    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": p.tool_id, "content": p.text or ""}
                    if p.is_error:
                        block["is_error"] = True
                    content.append(block)
                else:
                    content.append({"type": "image", "source": {"type": "base64", "media_type": p.mime, "data": p.data_b64}})
            messages.append({"role": role, "content": content or [{"type": "text", "text": "."}]})
        body: dict[str, Any] = {"model": req.model, "max_tokens": req.max_tokens, "system": req.system, "messages": messages}
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.tools:
            body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in req.tools]
        return body

    async def complete(self, cfg: ProviderConfig, req: Prepared) -> Completion:
        data = await transport.request_json(
            "POST", self.url(cfg, "/v1/messages"), headers=self._headers(cfg), body=self._body(req)
        )
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        calls = [PreparedCall(b["id"], b["name"], b.get("input") or {}) for b in data.get("content", []) if b.get("type") == "tool_use"]
        usage = data.get("usage", {})
        return Completion(text, usage.get("input_tokens", 0), usage.get("output_tokens", 0), finish_reason=norm_finish(data.get("stop_reason")),
                          info={"finish_reason": data.get("stop_reason")} if data.get("stop_reason") else {}, tool_calls=calls)

    async def stream(self, cfg: ProviderConfig, req: Prepared) -> AsyncIterator[str | Completion]:
        body = {**self._body(req), "stream": True}
        text, tin, tout, stop = "", 0, 0, None
        calls: list[PreparedCall] = []
        cur: dict[str, Any] | None = None  # the tool_use block being streamed
        async for ev in transport.stream_sse(self.url(cfg, "/v1/messages"), headers=self._headers(cfg), body=body):
            t = ev.get("type")
            if t == "message_start":
                tin = ev.get("message", {}).get("usage", {}).get("input_tokens", 0)
            elif t == "content_block_start" and ev.get("content_block", {}).get("type") == "tool_use":
                blk = ev["content_block"]
                cur = {"id": blk.get("id"), "name": blk.get("name"), "json": ""}
            elif t == "content_block_delta":
                delta = ev.get("delta", {})
                if delta.get("type") == "text_delta":
                    d = delta.get("text", "")
                    text += d
                    yield d
                elif delta.get("type") == "input_json_delta" and cur is not None:
                    cur["json"] += delta.get("partial_json", "")
            elif t == "content_block_stop" and cur is not None:
                try:
                    args = json.loads(cur["json"]) if cur["json"].strip() else {}
                except ValueError:
                    args = {}
                calls.append(PreparedCall(cur["id"], cur["name"], args if isinstance(args, dict) else {}))
                cur = None
            elif t == "message_delta":
                tout = ev.get("usage", {}).get("output_tokens", tout)
                stop = ev.get("delta", {}).get("stop_reason") or stop
            elif t == "error":
                e = ev.get("error", {})
                raise transport.ProviderReplyError(transport.error_kind(e.get("type")), f"provider error: {e.get('message') or e}")
        yield Completion(text, tin, tout, finish_reason=norm_finish(stop), info={"finish_reason": stop} if stop else {}, tool_calls=calls)

    async def list_models(self, cfg: ProviderConfig) -> list[dict[str, Any]]:
        data = await transport.request_json("GET", self.url(cfg, "/v1/models?limit=100"), headers=self._headers(cfg))
        return [{"id": m["id"], "label": m.get("display_name", m["id"]), "vision": True} for m in data.get("data", [])]

    def estimate_image_tokens(self, width: int, height: int) -> int:
        # Anthropic: images are downscaled to ~1.15 MP; tokens ≈ w*h/750.
        scale = min(1.0, (1_150_000 / max(1, width * height)) ** 0.5)
        return int(width * scale * height * scale / 750) + 1
