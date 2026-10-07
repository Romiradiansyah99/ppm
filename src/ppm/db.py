"""Postgres access. Plain SQL through psycopg - no ORM, framework-neutral rows
(plan section 9: data stays readable without LangChain or LangGraph)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ppm.config import get_settings

_pool: ConnectionPool | None = None
_app_pool: ConnectionPool | None = None


def get_pool(dsn: str | None = None) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=dsn or get_settings().dsn,
            min_size=1,
            max_size=4,
            configure=register_vector,
            open=True,
        )
    return _pool


def get_app_pool(dsn: str | None = None) -> ConnectionPool:
    """Pool for the silo-enforcing ppm_app role. Retrieval paths use this so
    row-level security applies; superusers would bypass it."""
    global _app_pool
    if _app_pool is None:
        app_dsn = dsn or get_settings().dsn_app
        if not app_dsn:
            raise RuntimeError("PPM_DSN_APP is not set - silo-enforcing retrieval needs the ppm_app role")
        _app_pool = ConnectionPool(
            conninfo=app_dsn,
            min_size=1,
            max_size=4,
            configure=register_vector,
            open=True,
        )
    return _app_pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Transactional connection: commits on clean exit, rolls back on error."""
    with get_pool().connection() as conn:
        yield conn


@contextmanager
def connection_app(person_id: str) -> Iterator[psycopg.Connection]:
    """Transactional connection as ppm_app with the person context set
    (transaction-local): RLS policies filter every query in this block."""
    with get_app_pool().connection() as conn:
        conn.execute("SELECT set_config('ppm.person_id', %s, true)", (str(person_id),))
        yield conn


def fetch_all_app(person_id: str, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    with connection_app(person_id) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_all(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
    """Drop the shared pools (tests, DSN changes)."""
    global _pool, _app_pool
    if _pool is not None:
        _pool.close()
        _pool = None
    if _app_pool is not None:
        _app_pool.close()
        _app_pool = None
