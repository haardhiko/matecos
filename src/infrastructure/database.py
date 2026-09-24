"""Async SQLAlchemy engine, session factory, and health check."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.config import AppSettings

# ── Naming convention for FK/index constraint names ──────────────────────────

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Module-level engine / session factory (set in init_db_engine)
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: AppSettings) -> AsyncEngine:
    """Create the async SQLAlchemy engine with configured pool settings."""
    return create_async_engine(
        settings.db.url,
        pool_size=settings.db.pool_size,
        max_overflow=settings.db.max_overflow,
        pool_timeout=settings.db.pool_timeout,
        echo=settings.db.echo,
        future=True,
    )


def init_db_engine(settings: AppSettings) -> None:
    """Initialize module-level engine and session factory. Call once at startup."""
    global _engine, _session_factory
    _engine = create_engine(settings)
    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine not initialized. Call init_db_engine() first.")
    return _engine


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager yielding a database session.
    Commits on success, rolls back on exception, always closes.
    """
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized. Call init_db_engine() first.")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables — for testing without migrations."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def check_db_health() -> bool:
    """Execute SELECT 1 to verify database connectivity. Returns True/False."""
    try:
        async with get_session() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def close_db() -> None:
    """Dispose engine connection pool. Call on shutdown."""
    if _engine is not None:
        await _engine.dispose()
