"""Database configuration and session management."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    # For type checkers, always import the real type
    from sqlalchemy.ext.asyncio import AsyncSession as AsyncSessionType
else:
    # At runtime, will be imported or set to Any
    AsyncSessionType = Any  # type: ignore[misc]

try:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.orm import DeclarativeBase

    # Database URL from environment or default to SQLite
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./vtt_transcribe.db")

    # Convert PostgreSQL URL to async version if needed
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif DATABASE_URL.startswith("sqlite://"):
        DATABASE_URL = DATABASE_URL.replace("sqlite://", "sqlite+aiosqlite://", 1)

    # Base class for all models
    class Base(DeclarativeBase):
        """Base class for all SQLAlchemy models."""

    # Create async engine
    engine = create_async_engine(
        DATABASE_URL,
        echo=os.getenv("SQL_ECHO", "false").lower() == "true",
        future=True,
    )

    # Create async session factory
    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    database_available = True

except ImportError:
    # Database dependencies not available
    Base = None  # type: ignore[assignment,misc]
    engine = None  # type: ignore[assignment]
    AsyncSessionLocal = None  # type: ignore[assignment]
    database_available = False


def _add_missing_columns(sync_conn: Any) -> None:
    """Tiny forward-only migration: add columns introduced after a table was first created."""
    from sqlalchemy import ClauseElement, inspect, text

    inspector = inspect(sync_conn)
    dialect = sync_conn.dialect
    for table in Base.metadata.sorted_tables:
        if table.name not in inspector.get_table_names():
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(dialect=dialect)}"
            default = column.server_default.arg if column.server_default is not None else None
            if default is not None:
                rendered = str(default.compile(dialect=dialect)) if isinstance(default, ClauseElement) else f"'{default}'"
                ddl += f" DEFAULT {rendered}"
            if not column.nullable and default is not None:
                ddl += " NOT NULL"
            sync_conn.execute(text(ddl))


async def init_db() -> None:
    """Initialize database tables."""
    if not database_available or engine is None:
        return  # Skip if database dependencies not available

    try:
        # Import models here to ensure they're registered with Base.metadata
        from vtt_server.models import APIKey, TranscriptionJob, User  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_add_missing_columns)
    except ImportError:
        # Models not available, skip initialization
        pass


async def get_db() -> AsyncGenerator[AsyncSessionType, None]:
    """Dependency for getting async database sessions."""
    if not database_available or AsyncSessionLocal is None:
        msg = "Database dependencies not available"
        raise RuntimeError(msg)

    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
