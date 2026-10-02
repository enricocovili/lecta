from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import config

engine = create_async_engine(
    config.database_url(),
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def notify(session: AsyncSession, channel: str, payload: str = "") -> None:
    from sqlalchemy import text

    await session.execute(text("SELECT pg_notify(:c, :p)"), {"c": channel, "p": payload})
