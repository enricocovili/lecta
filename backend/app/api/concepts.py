"""Concept map between courses (sections that appear in more than one course)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Course
from ..security.access import Access, access, readable
from ..services import concepts

router = APIRouter(dependencies=[Depends(access)])


@router.get("/concepts")
async def concept_map(acc: Access = Depends(access), db: AsyncSession = Depends(get_db)) -> dict:
    visible = set((await db.execute(select(Course.id).where(readable(acc.user)))).scalars())
    return await concepts.concept_map(db, visible)
