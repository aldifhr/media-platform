"""Raw parameterized SQL helper.

Split out of app/db_adapter.py. For DDL / aggregates the builder doesn't cover.
"""
from __future__ import annotations

from modules.manhwa.db.adapter import get_conn, put_conn
from modules.manhwa.db.query_builder import _row_to_jsonable


def q(sql: str, params: list | None = None) -> list[dict]:
    """Run a raw parameterized SQL statement and return rows.

    Convenience for DDL / aggregates that the builder doesn't cover
    (e.g. ``CREATE TABLE``, ``count(*) WHERE ...``). Reuses the
    pooled connection so callers don't touch get_conn()/put_conn()
    directly. NEVER interpolate values into ``sql`` — pass them via
    ``params`` (psycopg2 %s placeholders).
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or [])
        try:
            rows = cur.fetchall()
        except Exception:
            rows = []
        conn.commit()
        return [_row_to_jsonable(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
