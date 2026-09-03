#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Database connection — SQLite (zero external DB server).

The web layer uses SQLite via aiosqlite (async) so the server is portable and
needs nothing installed. The DB file defaults to ``<repo>/config/server.db``;
override with the ``DATABASE_URL`` env var (e.g. a Postgres URL) if desired.
Used ONLY in server mode (``SERVER_MODE=true``).
"""
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from .models import Base

SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'

# Default DB file: <repo>/config/server.db  (config/ already holds runtime state).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _REPO_ROOT / "config" / "server.db"


def _sync_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    _DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{_DEFAULT_DB.as_posix()}"


def _async_url(sync_url: str) -> str:
    # Map the sync driver to its async counterpart.
    if sync_url.startswith("sqlite:///"):
        return sync_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url


DATABASE_URL = _sync_url()
ASYNC_DATABASE_URL = _async_url(DATABASE_URL)

engine = None
async_engine = None
SessionLocal = None
AsyncSessionLocal = None

if SERVER_MODE:
    # Sync engine for alembic migrations.
    engine = create_engine(
        DATABASE_URL, echo=False,
        connect_args={"check_same_thread": False}
        if DATABASE_URL.startswith("sqlite") else {})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Async engine for FastAPI request handling.
    async_engine = create_async_engine(ASYNC_DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def _ensure_columns(sync_conn):
    """Lightweight SQLite migration: add columns that ``create_all`` won't add to an
    already-existing ``meetings`` table (create_all only creates missing TABLES)."""
    from sqlalchemy import text, inspect
    try:
        cols = {c["name"] for c in inspect(sync_conn).get_columns("meetings")}
    except Exception:
        return
    for col, ddl in (("progress", "INTEGER"), ("stage", "VARCHAR(40)"),
                     ("eta_seconds", "INTEGER"), ("project", "VARCHAR(100)"),
                     ("source_url", "VARCHAR(1000)")):
        if col not in cols:
            sync_conn.execute(text(f"ALTER TABLE meetings ADD COLUMN {col} {ddl}"))


async def init_db():
    """Create tables if they do not exist (SQLite bootstrap) + light migrations."""
    if not SERVER_MODE:
        print("WARNING: init_db called in desktop mode, skipping")
        return
    if async_engine is None:
        raise RuntimeError("Database engine not initialized. Set SERVER_MODE=true")
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_columns)
    print(f"Database ready: {ASYNC_DATABASE_URL}")


async def get_db():
    """FastAPI dependency yielding an async DB session."""
    if not SERVER_MODE or AsyncSessionLocal is None:
        raise RuntimeError("Database not available in desktop mode")
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_sync_db():
    """Sync session (migrations / scripts)."""
    if not SERVER_MODE or SessionLocal is None:
        raise RuntimeError("Database not available in desktop mode")
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
