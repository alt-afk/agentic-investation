"""
logger/db.py
============
Thin DB layer: one connection pool, one execute helper.
Nothing else lives here.

Environment
-----------
DATABASE_URL  – standard Postgres DSN, e.g.
                postgresql://user:pass@host:5432/dbname
"""

from __future__ import annotations

import logging
import os

import psycopg2
import psycopg2.extras   # for Json / RealDictCursor
import psycopg2.pool

log = logging.getLogger(__name__)

# Module-level pool — initialised once on first use (lazy, thread-safe).
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        dsn = os.environ["DATABASE_URL"]
        _pool = psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=20, dsn=dsn)
        log.info("DB pool initialised (min=2, max=20)")
    return _pool


def execute(sql: str, params: tuple) -> None:
    """Execute a single non-returning statement (INSERT / UPDATE) safely."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def fetchone(sql: str, params: tuple) -> dict | None:
    """Execute a SELECT and return one row as a dict, or None."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        pool.putconn(conn)