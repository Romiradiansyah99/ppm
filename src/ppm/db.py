"""Postgres access. Plain SQL through psycopg - no ORM, framework-neutral rows."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ppm.config import get_settings

_pool: ConnectionPool | None = None


def get_pool(dsn: str | None = None) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=dsn or get_settings().dsn,
            min_size=1,
            max_size=get_settings().db_pool_max,
            configure=register_vector,
            open=True,
        )
    return _pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Transactional connection: commits on clean exit, rolls back on error."""
    with get_pool().connection() as conn:
        yield conn


def fetch_all(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_all_access(
    access_level: str, sql: str, params: Sequence[Any] | None = None
) -> list[dict[str, Any]]:
    """Read-only query with the row-level access scope set for the transaction."""
    with connection() as conn:
        conn.rollback()
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SELECT set_config('ppm.access_level', %s, true)", (access_level,))
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def fetch_one(sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql: str, params: Sequence[Any] | None = None) -> int:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def reset_pool() -> None:
    """Drop the shared pool (tests, DSN changes)."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
