"""Public stats API — aggregate, non-sensitive counts for showcase/portfolio.

GET /api/public/stats
Everything here is safe to expose: no titles unless count > 0 aggregates,
no auth internals. Cached 60s at the FE proxy layer.

DB access goes through the shared pool (app.db_adapter.get_conn) and runs in
a worker thread (asyncio.to_thread) so the single uvicorn event loop is never
blocked on a synchronous psycopg2 round-trip.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

from modules.manhwa.utils.responses import err, ok

router = APIRouter()


def _stats() -> dict:
    from modules.manhwa.db_adapter import get_conn, put_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        out = {}
        cur.execute("SELECT COUNT(*) AS c FROM whitelist")
        out["series_tracked"] = cur.fetchone()["c"]
        cur.execute(
            "SELECT COUNT(DISTINCT LOWER(REPLACE(title_key,' ','-'))) AS c FROM whitelist"
        )
        out["series_unique"] = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM recent_chapters")
        out["chapters_indexed"] = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM dispatch_history")
        out["notifications_sent"] = cur.fetchone()["c"]
        cur.execute(
            "SELECT COUNT(*) AS c FROM dispatch_history WHERE sent_at >= now() - interval '24 hours'"
        )
        out["sent_last_24h"] = cur.fetchone()["c"]
        cur.execute(
            "SELECT COUNT(*) AS c FROM recent_chapters WHERE updated_time >= now() - interval '24 hours'"
        )
        out["chapters_last_24h"] = cur.fetchone()["c"]
        cur.execute(
            """SELECT COALESCE(AVG(c),0) AS a FROM (
                 SELECT date_trunc('day', updated_time) d, COUNT(DISTINCT chapter_url) c
                 FROM recent_chapters WHERE updated_time >= now() - interval '7 days'
                 GROUP BY 1) t"""
        )
        out["avg_chapters_per_day_7d"] = round(float(cur.fetchone()["a"]), 1)
        # sources active (health)
        try:
            cur.execute("SELECT source, status FROM source_health")
            rows = cur.fetchall()
            out["sources"] = {r["source"]: r["status"] for r in rows}
            out["sources_active"] = sum(1 for r in rows if (r["status"] or "").upper() == "HEALTHY")
        except Exception:
            out["sources"] = {}
            out["sources_active"] = 0
        # per-source breakdown
        cur.execute(
            """SELECT source, COUNT(*) AS c FROM recent_chapters 
               WHERE updated_time >= now() - interval '24 hours' 
               GROUP BY 1 ORDER BY 2 DESC"""
        )
        out["chapters_by_source_24h"] = {r["source"]: r["c"] for r in cur.fetchall()}
        return out
    finally:
        put_conn(conn)


@router.get("/public/stats")
async def public_stats():
    try:
        data = await asyncio.to_thread(_stats)
        data["service"] = "manhwa-backend"
        from datetime import datetime, timezone
        data["generated_at"] = datetime.now(timezone.utc).isoformat()
        return ok(
            data,
            headers={"Cache-Control": "public, max-age=60, stale-while-revalidate=120"},
        )
    except Exception:
        return err("internal server error", status_code=500)
