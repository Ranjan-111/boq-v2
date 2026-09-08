"""Async SQLAlchemy engine/session — Alembic-only schema authority.

docs/architecture.md decision log: NO create_all, NO schema-heal; migrations
are the single source of truth. Native uuid columns (PG uuid type via
SQLAlchemy Uuid).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

naming_convention: dict[str, Any] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata_naming = None  # populated via DeclarativeBase metadata below

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)


Base.metadata.naming_convention = naming_convention  # type: ignore[attr-defined]


def make_async_engine(url: str, **kwargs: Any) -> AsyncEngine:
    kwargs.setdefault("pool_pre_ping", True)
    return create_async_engine(url, **kwargs)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


# Sync engine ONLY for Alembic autogenerate (uses psycopg via URL swap in env.py).
def make_sync_engine(url: str) -> Any:
    sync_url = url.replace("+asyncpg", "+psycopg")
    return create_engine(sync_url)


__all__ = [
    "AsyncSession",
    "Base",
    "make_async_engine",
    "make_sessionmaker",
    "make_sync_engine",
    "naming_convention",
]
