"""Tool calling through the provider adapters (request shape and reply parsing), against a stubbed transport."""

from __future__ import annotations

import json

import pytest

from app.egress import transport
from app.egress.adapters.anthropic import AnthropicAdapter
from app.egress.adapters.base import Completion, Prepared, PreparedPart, ProviderConfig
from app.egress.adapters.gemini import GeminiAdapter
from app.egress.adapters.openai import OpenAIAdapter

TOOLS = [{"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]


def _req(model="m") -> Prepared:
    msgs = [
        ("user", [PreparedPart("text", text="leggi")]),
        ("assistant", [PreparedPart("text", text="ok"), PreparedPart("tool_call", tool_id="c1", tool_name="read_file", tool_args={"path": "a.tex"},
                                                                     tool_extra={"thoughtSignature": "sig"})]),
        ("user", [PreparedPart("image", data_b64="AAAA", mime="image/png"), PreparedPart("tool_result", text="contenuto", tool_id="c1", tool_name="read_file")]),
    ]
    return Prepared(model=model, system="sys", messages=msgs, max_tokens=100, temperature=0.2, json_output=False, task="t", request_key="k", tools=TOOLS)


def _cfg(t: str) -> ProviderConfig:
    return ProviderConfig(1, "p", t, "http://x", "key")


async def test_anthropic_tools(monkeypatch):
    seen = {}

    async def fake_json(method, url, headers, body=None, timeout=300):
        seen["body"] = body
        return {"content": [{"type": "text", "text": "ecco"}, {"type": "tool_use", "id": "t9", "name": "read_file", "input": {"path": "b.tex"}}],
                "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 3}}

    monkeypatch.setattr(transport, "request_json", fake_json)
    c = await AnthropicAdapter().complete(_cfg("anthropic"), _req())
    b = seen["body"]
    assert b["tools"] == [{"name": "read_file", "description": "read", "input_schema": TOOLS[0]["parameters"]}]
    assert b["messages"][1]["content"][1] == {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a.tex"}}
    # tool_result first in the user message, then the image.
    assert [x["type"] for x in b["messages"][2]["content"]] == ["tool_result", "image"]
    assert b["messages"][2]["content"][0]["tool_use_id"] == "c1"
    assert c.text == "ecco" and c.tool_calls[0].name == "read_file" and c.tool_calls[0].args == {"path": "b.tex"} and c.finish_reason == "stop"


async def test_anthropic_stream_tools(monkeypatch):
    async def fake_sse(url, headers, body, timeout=300):
        for ev in [
            {"type": "message_start", "message": {"usage": {"input_tokens": 7}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Leggo"}},
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1", "name": "grep"}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"pattern": '}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '"abc"}'}},
            {"type": "content_block_stop"},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}},
        ]:
            yield ev

    monkeypatch.setattr(transport, "stream_sse", fake_sse)
    out = [x async for x in AnthropicAdapter().stream(_cfg("anthropic"), _req())]
    assert out[0] == "Leggo" and isinstance(out[-1], Completion)
    assert out[-1].tool_calls[0].name == "grep" and out[-1].tool_calls[0].args == {"pattern": "abc"} and out[-1].tokens_in == 7 and out[-1].tokens_out == 9


async def test_openai_tools(monkeypatch):
    seen = {}

    async def fake_json(method, url, headers, body=None, timeout=300):
        seen["body"] = body
        return {"choices": [{"message": {"content": None, "tool_calls": [{"id": "x1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "c.tex"}'}}]},
                             "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}

    monkeypatch.setattr(transport, "request_json", fake_json)
    c = await OpenAIAdapter().complete(_cfg("openai"), _req())
    msgs = seen["body"]["messages"]
    assert seen["body"]["tools"][0]["function"]["name"] == "read_file"
    assert msgs[2]["tool_calls"][0]["function"]["arguments"] == '{"path": "a.tex"}' and msgs[2]["role"] == "assistant"
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "contenuto"}
    assert msgs[4]["role"] == "user" and msgs[4]["content"][0]["type"] == "image_url"
    assert c.tool_calls[0].args == {"path": "c.tex"} and c.finish_reason == "stop"


async def test_openai_stream_tools(monkeypatch):
    async def fake_sse(url, headers, body, timeout=300):
        for ev in [
            {"choices": [{"delta": {"content": "Vedo"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "z", "function": {"name": "read_", "arguments": '{"pa'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "file", "arguments": 'th": "d.tex"}'}}]}, "finish_reason": "tool_calls"}]},
            {"usage": {"prompt_tokens": 3, "completion_tokens": 1}, "choices": []},
        ]:
            yield ev

    monkeypatch.setattr(transport, "stream_sse", fake_sse)
    out = [x async for x in OpenAIAdapter().stream(_cfg("openai"), _req())]
    call = out[-1].tool_calls[0]
    assert (call.id, call.name, call.args) == ("z", "read_file", {"path": "d.tex"}) and out[-1].tokens_in == 3


async def test_gemini_tools(monkeypatch):
    seen = {}

    async def fake_json(method, url, headers, body=None, timeout=300):
        seen["body"] = body
        return {"candidates": [{"content": {"parts": [{"functionCall": {"name": "read_file", "args": {"path": "e.tex"}}, "thoughtSignature": "s2"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1}}

    monkeypatch.setattr(transport, "request_json", fake_json)
    c = await GeminiAdapter().complete(_cfg("gemini"), _req())
    b = seen["body"]
    assert b["tools"][0]["functionDeclarations"][0]["name"] == "read_file"
    assert b["contents"][1]["parts"][1] == {"functionCall": {"name": "read_file", "args": {"path": "a.tex"}}, "thoughtSignature": "sig"}
    assert b["contents"][2]["parts"][0] == {"functionResponse": {"name": "read_file", "response": {"result": "contenuto"}}}
    assert c.tool_calls[0].args == {"path": "e.tex"} and c.tool_calls[0].extra == {"thoughtSignature": "s2"}


@pytest.mark.parametrize("adapter", [AnthropicAdapter(), OpenAIAdapter(), GeminiAdapter()])
async def test_no_tools_no_tool_fields(adapter, monkeypatch):
    seen = {}

    async def fake_json(method, url, headers, body=None, timeout=300):
        seen["body"] = body
        return {"content": [], "choices": [{"message": {"content": "x"}}], "candidates": [{"content": {"parts": [{"text": "x"}]}}]}

    monkeypatch.setattr(transport, "request_json", fake_json)
    req = Prepared(model="m", system="s", messages=[("user", [PreparedPart("text", text="hi")])], max_tokens=10, temperature=None, json_output=False, task="t", request_key="k")
    await adapter.complete(_cfg(adapter.type), req)
    assert "tools" not in seen["body"] and json.dumps(seen["body"])
