"""PostgreSQL connection pool + borrowed-connection lifecycle.

Split out of app/db_adapter.py. The builder and raw helpers import get_conn /
put_conn from here.
"""
from __future__ import annotations

import os
import threading

import psycopg2
import psycopg2.pool
from psycopg2.extras import RealDictCursor

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger
from modules.manhwa.core.resilience import cb_db

logger = get_logger("db")

DATABASE_URL = os.environ.get("DATABASE_URL") or getattr(settings, "DATABASE_URL", "") or ""

_pool = None
_pool_lock = threading.Lock()
# Bounded semaphore so we never exceed pool capacity; get_conn() blocks
# (queues) instead of raising PoolError under burst load.
_conn_sem = None
# Telemetry counters for pool health.
_pool_acquires = 0
_pool_releases = 0
_pool_active = 0
_POOL_MAX = 15


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool, _conn_sem
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if not DATABASE_URL:
                    raise RuntimeError("DATABASE_URL not set (transaction pooler DSN required)")
                # Timezone is set via SET TIME ZONE in get_conn(); don't modify DSN
                # because some psycopg2 parsers reject options= in URI query params.
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=_POOL_MAX,
                    dsn=DATABASE_URL,
                    cursor_factory=RealDictCursor,
                )
                _conn_sem = threading.Semaphore(_POOL_MAX)
                logger.info("psycopg2 pool created", dsn_masked=("***" if "@" in DATABASE_URL else DATABASE_URL.split("?")[0]))
    return _pool


def get_pool_stats() -> dict:
    return {
        "active": _pool_active,
        "idle": max(0, _POOL_MAX - _pool_active),
        "max": _POOL_MAX,
        "acquires": _pool_acquires,
        "releases": _pool_releases,
    }


def get_conn(maintenance: bool = False):
    global _pool_acquires, _pool_releases, _pool_active
    if not cb_db.allow():
        raise RuntimeError("circuit db OPEN — fast fail")
    sem = _get_pool() and _conn_sem
    if sem is None:
        _get_pool()
        sem = _conn_sem
    sem.acquire()
    _pool_acquires += 1
    last_err = None
    for _ in range(2):
        try:
            conn = _get_pool().getconn()
            if getattr(conn, "closed", 0):
                raise psycopg2.OperationalError("connection marked closed")
            try:
                conn.autocommit = True
            except Exception:
                pass
            # Force UTC on every borrowed connection. The DB default session
            # TZ is Asia/Shanghai (+08:00), which makes tz-naive/string
            # `updated_time >= cutoff` comparisons shift by 8h and silently
            # drop rows from RSS/cron windows. UTC makes all comparisons
            # unambiguous regardless of how the param is bound.
            try:
                with conn.cursor() as _tzcur:
                    _tzcur.execute("SET TIME ZONE UTC")
                    # Maintenance (VACUUM/ANALYZE) legitimately runs longer
                    # than any interactive query; the 10s cap that protects
                    # the app from runaway statements would abort it. App
                    # queries keep the cap — only maintenance opts out.
                    if maintenance:
                        _tzcur.execute("SET statement_timeout = '0'")
                    else:
                        _tzcur.execute("SET statement_timeout = '10000'")
            except Exception:
                pass
            cb_db.record_success()
            _pool_active += 1
            return conn
        except Exception as e:
            last_err = e
            cb_db.record_failure()
            try:
                _get_pool().putconn(conn, close=True)
            except Exception:
                pass
    # Both attempts failed.
    _pool_releases += 1
    sem.release()
    raise last_err


def put_conn(conn):
    global _pool_active
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass
    finally:
        _pool_active -= 1
        if _conn_sem is not None:
            _conn_sem.release()


def close_pool() -> None:
    """Close all connections in the pool."""
    global _pool, _conn_sem
    if _pool is not None:
        try:
            _pool.closeall()
        except Exception:
            pass
        _pool = None
        _conn_sem = None
