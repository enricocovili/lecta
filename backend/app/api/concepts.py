"""Concept map between courses (sections that appear in more than one course)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..security.auth import require_admin
from ..services import concepts

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/concepts")
async def concept_map(db: AsyncSession = Depends(get_db)) -> dict:
    return await concepts.concept_map(db)
