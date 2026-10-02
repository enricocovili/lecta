"""Application settings stored in the database (edited from the web UI).

Each section is a Pydantic model. Stored values are merged over the defaults,
so adding a field later never breaks an existing installation.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Setting


class SiteSettings(BaseModel):
    title: str = "Lecta"
    description: str = "Appunti universitari"
    # Small kicker above the landing title, e.g. "Enrico Covili · Unimore".
    byline: str = Field("", max_length=200)
    allow_indexing: bool = False


class LatexSettings(BaseModel):
    engine: Literal["pdflatex", "xelatex", "lualatex"] = "pdflatex"
    timeout_s: int = Field(120, ge=10, le=1800)
    # After an import, AI fixes allowed when the new chapter doesn't compile (0 = no check at all).
    autofix_iterations: int = Field(1, ge=0, le=3)
    auto_compile_on_save: bool = False


class TemplateSettings(BaseModel):
    preamble: str | None = None  # None = shipped default (services/templates.py)


class UploadSettings(BaseModel):
    max_file_mb: int = Field(1024, ge=1, le=20480)
    max_zip_uncompressed_mb: int = Field(4096, ge=1, le=102400)
    max_zip_members: int = Field(5000, ge=1, le=100000)
    max_compression_ratio: int = Field(200, ge=2, le=10000)


class EmbeddingSettings(BaseModel):
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # or "off"
    threads: int = Field(2, ge=1, le=4)
    min_chunks_per_s: float = Field(2.0, ge=0)
    benchmark: dict[str, Any] | None = None  # written by the worker


class CategorizationSettings(BaseModel):
    threshold: float = Field(0.55, ge=0, le=1)
    top_k: int = Field(8, ge=1, le=50)


class RoleTarget(BaseModel):
    provider_id: int | None = None
    model: str | None = None


class RoleAssignment(BaseModel):
    primary: RoleTarget = RoleTarget()
    fallback: RoleTarget = RoleTarget()


ROLES = ("vision", "handwriting", "writing", "classification", "chat")
ROLE_LABELS = {
    "vision": "Reading pages with their picture (vision)",
    "handwriting": "Handwriting transcription (vision)",
    "writing": "Reading text pages / fixes",
    "classification": "Placement",
    "chat": "AI assistant (chat with tools)",
}


class RoleSettings(BaseModel):
    vision: RoleAssignment = RoleAssignment()
    handwriting: RoleAssignment = RoleAssignment()
    writing: RoleAssignment = RoleAssignment()
    classification: RoleAssignment = RoleAssignment()
    chat: RoleAssignment = RoleAssignment()


class AISettings(BaseModel):
    max_output_tokens: int = Field(8000, ge=256, le=128000)
    chunk_pages: int = Field(10, ge=1, le=100)  # pages per reading unit
    chunk_chars: int = Field(24000, ge=2000, le=400000)
    request_timeout_s: int = Field(300, ge=10, le=3600)
    max_concurrent_requests: int = Field(4, ge=1, le=16)
    # Model calls (tool rounds) the assistant may use in one turn.
    agent_max_steps: int = Field(40, ge=5, le=200)


SECTIONS: dict[str, type[BaseModel]] = {
    "site": SiteSettings,
    "latex": LatexSettings,
    "template": TemplateSettings,
    "uploads": UploadSettings,
    "embeddings": EmbeddingSettings,
    "categorization": CategorizationSettings,
    "roles": RoleSettings,
    "ai": AISettings,
}


async def get_section(db: AsyncSession, key: str) -> Any:
    model = SECTIONS[key]
    row = await db.get(Setting, key)
    stored = row.value if row and isinstance(row.value, dict) else {}
    return model.model_validate({**model().model_dump(), **stored})


async def set_section(db: AsyncSession, key: str, value: dict[str, Any], *, commit: bool = True) -> Any:
    model = SECTIONS[key]
    current = await get_section(db, key)
    merged = model.model_validate({**current.model_dump(), **value})
    stmt = insert(Setting).values(key=key, value=merged.model_dump(mode="json"))
    stmt = stmt.on_conflict_do_update(index_elements=[Setting.key], set_={"value": stmt.excluded.value})
    await db.execute(stmt)
    if commit:
        await db.commit()
    return merged


async def all_sections(db: AsyncSession) -> dict[str, Any]:
    return {k: (await get_section(db, k)).model_dump(mode="json") for k in SECTIONS}
