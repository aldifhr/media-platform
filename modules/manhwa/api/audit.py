"""Audit — single domain for admin forensic. Consolidates audit_log + audit_stale."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.manhwa.logger import get_logger
from modules.manhwa.utils.request_auth import require_monitor_auth, int_safe, safe_error
from modules.manhwa.utils.responses import ok, unauthorized

logger = get_logger("api:audit")
router = APIRouter()


@router.get("/audit-log")
async def get_audit_log(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        action = request.query_params.get("action", "")
        resource = request.query_params.get("resource", "")
        limit = int_safe(request.query_params.get("limit", "50"), 50, max_val=200)
        offset = int_safe(request.query_params.get("offset", "0"), 0, max_val=100000)
        from modules.manhwa.db import get_supabase
        sb = get_supabase()
        q = sb.table("audit_log").select("*", count="exact").order("timestamp", desc=True).limit(limit).offset(offset)
        if action:
            q = q.eq("action", action)
        if resource:
            q = q.eq("resource", resource)
        res = q.execute()
        return ok({"results": res.data or [], "total": res.count or 0, "limit": limit, "offset": offset})
    except Exception as e:
        if "does not exist" in str(e):
            return ok({"results": [], "total": 0, "limit": limit, "offset": offset})
        logger.warn("audit_log fetch failed", err=str(e)[:200])
        return JSONResponse(content=safe_error(e), status_code=500)


@router.get("/audit/stale")
async def audit_stale(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        # int() on a raw query param raises ValueError on ?days=abc, which was
        # a 500. int_safe falls back to the default instead.
        days = int_safe(request.query_params.get("days"), 30)
        days = max(7, min(days, 365))
        limit = int_safe(request.query_params.get("limit"), 20)
        limit = max(1, min(limit, 100))
        from modules.manhwa.db import q
        rows = q("""
            SELECT w.title_key, w.source, w.title,
                   COALESCE(GREATEST(rc2.rc_max, dh.dh_max, sm.sm_max), rc2.rc_max, dh.dh_max, sm.sm_max) as last_update,
                   EXTRACT(DAY FROM NOW() - COALESCE(GREATEST(rc2.rc_max, dh.dh_max, sm.sm_max), rc2.rc_max, dh.dh_max, sm.sm_max))::int as days_idle,
                   COALESCE(rc2.cnt,0)::int as chapter_count
            FROM whitelist w
            LEFT JOIN (SELECT title_key, source, MAX(updated_time) as rc_max, COUNT(*) as cnt FROM recent_chapters GROUP BY title_key, source) rc2
                   ON rc2.title_key = w.title_key AND rc2.source = w.source
            LEFT JOIN (SELECT title_key, source, MAX(sent_at) as dh_max FROM dispatch_history GROUP BY title_key, source) dh
                   ON dh.title_key = w.title_key AND dh.source = w.source
            LEFT JOIN (SELECT title_key, source, MAX(updated_at) as sm_max FROM series_meta GROUP BY title_key, source) sm
                   ON sm.title_key = w.title_key AND sm.source = w.source
            WHERE COALESCE(GREATEST(rc2.rc_max, dh.dh_max, sm.sm_max), rc2.rc_max, dh.dh_max, sm.sm_max) IS NULL
               OR COALESCE(GREATEST(rc2.rc_max, dh.dh_max, sm.sm_max), rc2.rc_max, dh.dh_max, sm.sm_max) < NOW() - (%s || ' days')::interval
            ORDER BY 4 ASC NULLS FIRST
            LIMIT %s
        """, [str(days), str(limit)])
        return ok({"results": rows, "days": days, "limit": limit})
    except Exception as e:
        logger.error("audit_stale failed", exc=e)
        return JSONResponse(content=safe_error(e), status_code=500)
