"""Builders for the EgressRequests the pipelines send.

Everything that comes from uploads (including file and folder names) or from
models is fenced with a per-request nonce, so the model can tell it apart from
the instructions (prompt-injection defence).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..egress import gate
from ..egress.schemas import EgressRequest, Message, Part
from ..services import blobs, prompts
from ..services import settings as settings_svc


class RequestBuilder:
    def __init__(self, prompt_key: str):
        self.prompt_key = prompt_key
        self.nonce = gate.new_nonce()
        self.parts: list[Part] = []

    def instr(self, text: str) -> RequestBuilder:
        self.parts.append(Part(type="text", text=text))
        return self

    def data(self, text: str, label: str) -> RequestBuilder:
        self.parts.append(Part(type="text", text=prompts.fence(text, self.nonce, label), label=label))
        return self

    def image(self, blob: str, width: int | None, height: int | None, label: str, mime: str | None = None) -> RequestBuilder:
        self.parts.append(Part(type="image", blob=blob, width=width, height=height, mime=mime or image_mime(blob), label=label))
        return self

    async def build(
        self,
        db: AsyncSession,
        *,
        role: str,
        task: str,
        request_key: str,
        meta: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        json_output: bool = True,
        temperature: float | None = 0.2,
    ) -> EgressRequest:
        system, _tag = await prompts.render(db, self.prompt_key, self.nonce)
        ai = await settings_svc.get_section(db, "ai")
        return EgressRequest(
            role=role,
            task=task,
            request_key=request_key,
            system=system,
            messages=[Message(role="user", parts=self.parts)],
            max_tokens=min(max_tokens or ai.max_output_tokens, ai.max_output_tokens),
            temperature=temperature,
            json_output=json_output,
            meta=meta or {},
        )


def image_mime(blob: str) -> str:
    """The media type of a stored image (providers reject mismatching types)."""
    with open(blobs.path_for(blob), "rb") as f:
        head = f.read(4)
    return "image/jpeg" if head[:3] == b"\xff\xd8\xff" else "image/png"


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)
