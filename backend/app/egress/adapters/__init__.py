"""Provider adapters, used by app.egress.gate."""

from __future__ import annotations

from .anthropic import AnthropicAdapter
from .base import Adapter
from .fake import FakeAdapter
from .gemini import GeminiAdapter
from .openai import OpenAIAdapter, OpenAICompatAdapter

ADAPTERS: dict[str, Adapter] = {
    a.type: a for a in (AnthropicAdapter(), OpenAIAdapter(), OpenAICompatAdapter(), GeminiAdapter(), FakeAdapter())
}
