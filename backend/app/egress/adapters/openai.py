"""OpenAI Chat Completions, and any OpenAI-compatible endpoint
(OpenRouter, Mistral, vLLM, Ollama, LM Studio, ...)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from .. import transport
from .base import Adapter, Completion, Prepared, PreparedCall, ProviderConfig, norm_finish


def _reply_error(err: dict[str, Any]) -> transport.ProviderReplyError:
    code = err.get("code")
    meta = err.get("metadata") if isinstance(err.get("metadata"), dict) else {}
    msg = f"provider error {code}: {err.get('message') or 'no message'}" if code else f"provider error: {err.get('message') or err}"
    if meta.get("provider_name"):
        msg += f" (upstream: {meta['provider_name']})"
    raw = meta.get("raw")
    if raw:
        msg += f" — {str(raw)[:300]}"
    return transport.ProviderReplyError(transport.error_kind(code), msg)


class OpenAIAdapter(Adapter):
    type = "openai"
    label = "OpenAI"
    default_base_url = "https://api.openai.com/v1"
    max_tokens_field = "max_completion_tokens"

    def _headers(self, cfg: ProviderConfig) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if cfg.api_key:
            h["authorization"] = f"Bearer {cfg.api_key}"
        for k, v in (cfg.options.get("headers") or {}).items():
            h[str(k)] = str(v)
        return h

    def _body(self, cfg: ProviderConfig, req: Prepared) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": req.system}]
        for role, parts in req.messages:
            calls = [p for p in parts if p.type == "tool_call"]
            if calls:
                text = "\n\n".join(p.text or "" for p in parts if p.type == "text")
                messages.append({
                    "role": "assistant", "content": text or None,
                    "tool_calls": [{"id": p.tool_id, "type": "function",
                                    "function": {"name": p.tool_name, "arguments": json.dumps(p.tool_args or {}, ensure_ascii=False)}} for p in calls],
                })
                continue
            # Tool results are messages of their own (they must directly follow the assistant's tool_calls).
            for p in parts:
                if p.type == "tool_result":
                    messages.append({"role": "tool", "tool_call_id": p.tool_id, "content": p.text or ""})
            parts = [p for p in parts if p.type != "tool_result"]
            if not parts:
                continue
            if all(p.type == "text" for p in parts):
                messages.append({"role": role, "content": "\n\n".join(p.text or "" for p in parts)})
                continue
            content = []
            for p in parts:
                if p.type == "text":
                    content.append({"type": "text", "text": p.text or ""})
                else:
                    content.append({"type": "image_url", "image_url": {"url": f"data:{p.mime};base64,{p.data_b64}"}})
            messages.append({"role": role, "content": content})
        body: dict[str, Any] = {"model": req.model, "messages": messages}
        if req.tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
                             for t in req.tools]
        field = cfg.options.get("max_tokens_field") or self.max_tokens_field
        body[field] = req.max_tokens
        if req.temperature is not None and not cfg.options.get("no_temperature"):
            body["temperature"] = req.temperature
        if req.json_output and cfg.options.get("json_mode", True):
            body["response_format"] = {"type": "json_object"}
        return body

    async def complete(self, cfg: ProviderConfig, req: Prepared) -> Completion:
        data = await transport.request_json(
            "POST", self.url(cfg, "/chat/completions"), headers=self._headers(cfg), body=self._body(cfg, req)
        )
        # OpenRouter & co. report upstream failures (rate limits, provider outages) as an
        # `error` object, sometimes with HTTP 200 and no choices.
        if isinstance(data.get("error"), dict):
            raise _reply_error(data["error"])
        choice = (data.get("choices") or [{}])[0]
        if isinstance(choice.get("error"), dict):
            raise _reply_error(choice["error"])
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        usage = data.get("usage") or {}
        calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append(PreparedCall(tc.get("id") or f"call_{len(calls)}", fn.get("name", ""), args if isinstance(args, dict) else {}))
        info = {
            "finish_reason": choice.get("finish_reason"),
            "native_finish_reason": choice.get("native_finish_reason"),
            "upstream": data.get("provider"),
            "refusal": message.get("refusal"),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "choices": len(data.get("choices") or []),
        }
        return Completion(
            content, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
            finish_reason="refusal" if message.get("refusal") else norm_finish(choice.get("finish_reason")),
            info={k: v for k, v in info.items() if v not in (None, "")}, tool_calls=calls,
        )

    async def stream(self, cfg: ProviderConfig, req: Prepared) -> AsyncIterator[str | Completion]:
        body = {**self._body(cfg, req), "stream": True, "stream_options": {"include_usage": True}}
        text, tin, tout, finish = "", 0, 0, None
        acc: dict[int, dict[str, str]] = {}  # streamed tool calls by index
        async for ev in transport.stream_sse(self.url(cfg, "/chat/completions"), headers=self._headers(cfg), body=body):
            if isinstance(ev.get("error"), dict):
                raise _reply_error(ev["error"])
            for ch in ev.get("choices") or []:
                delta = ch.get("delta") or {}
                d = delta.get("content") or ""
                if d:
                    text += d
                    yield d
                for tc in delta.get("tool_calls") or []:
                    slot = acc.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "args": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["args"] += fn.get("arguments") or ""
                finish = ch.get("finish_reason") or finish
            if ev.get("usage"):
                tin = ev["usage"].get("prompt_tokens", tin)
                tout = ev["usage"].get("completion_tokens", tout)
        calls = []
        for i in sorted(acc):
            slot = acc[i]
            try:
                args = json.loads(slot["args"]) if slot["args"].strip() else {}
            except ValueError:
                args = {}
            calls.append(PreparedCall(slot["id"] or f"call_{i}", slot["name"], args if isinstance(args, dict) else {}))
        yield Completion(text, tin, tout, finish_reason=norm_finish(finish), info={"finish_reason": finish} if finish else {}, tool_calls=calls)

    async def list_models(self, cfg: ProviderConfig) -> list[dict[str, Any]]:
        data = await transport.request_json("GET", self.url(cfg, "/models"), headers=self._headers(cfg))
        return [{"id": m["id"], "label": m.get("name", m["id"])} for m in data.get("data", []) if "id" in m]

    def estimate_image_tokens(self, width: int, height: int) -> int:
        # High-detail tiling: 85 + 170 per 512 px tile after fitting in 2048/768.
        scale = min(1.0, 2048 / max(width, height))
        w, h = width * scale, height * scale
        scale = min(1.0, 768 / min(w, h))
        w, h = w * scale, h * scale
        tiles = -(-int(w) // 512) * -(-int(h) // 512)
        return 85 + 170 * tiles


class OpenAICompatAdapter(OpenAIAdapter):
    type = "openai_compat"
    label = "Compatibile OpenAI (OpenRouter, Mistral, vLLM, Ollama, LM Studio…)"
    default_base_url = "http://localhost:11434/v1"
    max_tokens_field = "max_tokens"
    needs_key = False
