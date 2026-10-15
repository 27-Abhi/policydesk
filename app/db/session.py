"""Database connection pooling and lifecycle management for PolicyDesk."""

import logging
import os
from pathlib import Path
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

logger = logging.getLogger("policydesk.db")

_async_pool: Optional[AsyncConnectionPool] = None
_sync_pool: Optional[ConnectionPool] = None


def get_database_url() -> Optional[str]:
    """Retrieve normalized PostgreSQL connection URL from environment."""
    url = os.getenv("DATABASE_URL")
    if url and url.strip():
        # Handle postgres:// vs postgresql:// normalization
        clean_url = url.strip()
        if clean_url.startswith("postgres://"):
            clean_url = "postgresql://" + clean_url[len("postgres://"):]
        return clean_url
    return None


def is_postgres_configured() -> bool:
    """Check if PostgreSQL credentials are present in the environment."""
    return get_database_url() is not None


def get_sync_pool() -> Optional[ConnectionPool]:
    """Retrieve or initialize synchronous connection pool for migrations and background workers."""
    global _sync_pool
    db_url = get_database_url()
    if not db_url:
        return None
    if _sync_pool is None:
        _sync_pool = ConnectionPool(
            conninfo=db_url,
            min_size=1,
            max_size=4,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        logger.info("[DB:POOL] Synchronous connection pool initialized (1..4 connections).")
    return _sync_pool


async def get_async_pool() -> Optional[AsyncConnectionPool]:
    """Retrieve or initialize asynchronous connection pool for FastAPI and LangGraph async nodes."""
    global _async_pool
    db_url = get_database_url()
    if not db_url:
        return None
    if _async_pool is None:
        _async_pool = AsyncConnectionPool(
            conninfo=db_url,
            min_size=2,
            max_size=8,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        await _async_pool.open()
        logger.info("[DB:POOL] Asynchronous connection pool initialized (2..8 connections).")
    return _async_pool


async def close_pools() -> None:
    """Close connection pools gracefully on application shutdown."""
    global _async_pool, _sync_pool
    if _async_pool is not None:
        await _async_pool.close()
        _async_pool = None
        logger.info("[DB:POOL] Asynchronous connection pool closed.")
    if _sync_pool is not None:
        _sync_pool.close()
        _sync_pool = None
        logger.info("[DB:POOL] Synchronous connection pool closed.")


def init_database() -> bool:
    """Execute schema.sql migrations synchronously if PostgreSQL is configured."""
    db_url = get_database_url()
    if not db_url:
        logger.info("[DB:INIT] DATABASE_URL not set; skipping PostgreSQL initialization.")
        return False

    schema_file = Path(__file__).resolve().parent / "schema.sql"
    if not schema_file.exists():
        logger.warning("[DB:INIT] schema.sql not found at %s", schema_file)
        return False

    try:
        with psycopg.connect(db_url, autocommit=True) as conn:
            sql = schema_file.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
            logger.info("[DB:INIT] PostgreSQL schema and seed data successfully initialized.")
            return True
    except Exception as exc:
        logger.error("[DB:INIT] Failed to initialize PostgreSQL schema: %s", exc)
        return False
