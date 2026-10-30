"""Async database engine + session factory + migration discovery.

Multi-statement migration *execution* (advisory-locked runner) lands in Phase 7
with the real schema; Phase 2 ships engine/session/ping + file discovery.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import get_settings
from core.logging import get_logger

log = get_logger("core:database")

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True, future=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    return _sessionmaker


async def ping() -> bool:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("db ping failed", err=str(e)[:160])
        return False


def list_migrations(directory: str | Path) -> list[Path]:
    return sorted(Path(directory).glob("*.sql"), key=lambda p: p.name)
