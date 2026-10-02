"""Google Gemini (Generative Language API)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .. import transport
from .base import Adapter, Completion, Prepared, PreparedCall, ProviderConfig, norm_finish


class GeminiAdapter(Adapter):
    type = "gemini"
    label = "Google Gemini"
    default_base_url = "https://generativelanguage.googleapis.com"

    def _headers(self, cfg: ProviderConfig) -> dict[str, str]:
        return {"x-goog-api-key": cfg.api_key or "", "content-type": "application/json"}

    def _body(self, req: Prepared) -> dict[str, Any]:
        contents = []
        for role, parts in req.messages:
            ps = []
            for p in sorted(parts, key=lambda x: 0 if x.type == "tool_result" else 1):
                if p.type == "text":
                    if p.text:
                        ps.append({"text": p.text})
                elif p.type == "tool_call":
                    part: dict[str, Any] = {"functionCall": {"name": p.tool_name, "args": p.tool_args or {}}}
                    if (p.tool_extra or {}).get("thoughtSignature"):
                        part["thoughtSignature"] = p.tool_extra["thoughtSignature"]
                    ps.append(part)
                elif p.type == "tool_result":
                    ps.append({"functionResponse": {"name": p.tool_name, "response": {"error" if p.is_error else "result": p.text or ""}}})
                else:
                    ps.append({"inline_data": {"mime_type": p.mime, "data": p.data_b64}})
            contents.append({"role": "model" if role == "assistant" else "user", "parts": ps or [{"text": "."}]})
        gen: dict[str, Any] = {"maxOutputTokens": req.max_tokens}
        if req.temperature is not None:
            gen["temperature"] = req.temperature
        if req.json_output:
            gen["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"systemInstruction": {"parts": [{"text": req.system}]}, "contents": contents, "generationConfig": gen}
        if req.tools:
            body["tools"] = [{"functionDeclarations": [{"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
                                                      for t in req.tools]}]
        return body

    @staticmethod
    def _calls(parts: list[dict[str, Any]]) -> list[PreparedCall]:
        out = []
        for p in parts:
            fc = p.get("functionCall")
            if fc:
                extra = {"thoughtSignature": p["thoughtSignature"]} if p.get("thoughtSignature") else {}
                out.append(PreparedCall(f"call_{len(out)}", fc.get("name", ""), fc.get("args") or {}, extra))
        return out

    @staticmethod
    def _model_path(model: str) -> str:
        return model if model.startswith("models/") else f"models/{model}"

    async def complete(self, cfg: ProviderConfig, req: Prepared) -> Completion:
        url = self.url(cfg, f"/v1beta/{self._model_path(req.model)}:generateContent")
        data = await transport.request_json("POST", url, headers=self._headers(cfg), body=self._body(req))
        cand = (data.get("candidates") or [{}])[0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = data.get("usageMetadata") or {}
        fr = cand.get("finishReason") or (data.get("promptFeedback") or {}).get("blockReason")
        return Completion(text, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0), finish_reason=norm_finish(fr),
                          info={"finish_reason": fr} if fr else {}, tool_calls=self._calls(parts))

    async def stream(self, cfg: ProviderConfig, req: Prepared) -> AsyncIterator[str | Completion]:
        url = self.url(cfg, f"/v1beta/{self._model_path(req.model)}:streamGenerateContent?alt=sse")
        text, tin, tout, fr = "", 0, 0, None
        calls: list[PreparedCall] = []
        async for ev in transport.stream_sse(url, headers=self._headers(cfg), body=self._body(req)):
            for cand in ev.get("candidates") or []:
                parts = cand.get("content", {}).get("parts", [])
                for p in parts:
                    d = p.get("text", "")
                    if d and not p.get("thought"):
                        text += d
                        yield d
                for c in self._calls(parts):
                    c.id = f"call_{len(calls)}"
                    calls.append(c)
                fr = cand.get("finishReason") or fr
            u = ev.get("usageMetadata") or {}
            tin = u.get("promptTokenCount", tin)
            tout = u.get("candidatesTokenCount", tout)
        yield Completion(text, tin, tout, finish_reason=norm_finish(fr), info={"finish_reason": fr} if fr else {}, tool_calls=calls)

    async def list_models(self, cfg: ProviderConfig) -> list[dict[str, Any]]:
        data = await transport.request_json("GET", self.url(cfg, "/v1beta/models?pageSize=200"), headers=self._headers(cfg))
        return [
            {"id": m["name"].removeprefix("models/"), "label": m.get("displayName", m["name"]), "vision": True}
            for m in data.get("models", [])
            if "generateContent" in (m.get("supportedGenerationMethods") or [])
        ]

    def estimate_image_tokens(self, width: int, height: int) -> int:
        return 258
