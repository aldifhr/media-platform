"""Error logs storage — persistent journal for backend errors."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from modules.manhwa.db import get_supabase
from modules.manhwa.core.text import escape_like
from modules.manhwa.core.time import cutoff_iso
from modules.manhwa.logger import get_logger, get_correlation_id

logger = get_logger("storage:error_logs")

# insert_error must never log through the app logger. The app logger feeds
# error_logs back into itself (logger._worker calls insert_error), so a failing
# insert produced a warn, which was queued, which called insert_error again,
# which failed again — a tight loop that wrote 140k lines in five minutes while
# the DB circuit was open. Report to stderr at most once a minute instead.
_LAST_WARN = 0.0


def _warn_once(err: str) -> None:
    global _LAST_WARN
    import time

    now = time.monotonic()
    if now - _LAST_WARN < 60:
        return
    _LAST_WARN = now
    try:
        import sys

        sys.stderr.write(f'{{"level": "warn", "scope": "storage:error_logs", '
                         f'"msg": "insert_error failed", "err": {err[:200]!r}}}\n')
        sys.stderr.flush()
    except Exception:
        pass



def insert_error(
    level: str = "error",
    source: str = "app",
    message: str = "",
    stack: str | None = None,
    path: str | None = None,
    correlation_id: str | None = None,
    meta: dict | None = None,
) -> None:
    try:
        cid = correlation_id or get_correlation_id()
        row = {
            "level": level,
            "source": source,
            "message": message[:2000] if message else "",
            "stack": (stack or "")[:8000] if stack else None,
            "path": (path or "")[:500] if path else None,
            "correlation_id": (cid or "")[:100] if cid else None,
            "meta": meta or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        get_supabase().table("error_logs").insert(row).execute()
    except Exception as e:
        _warn_once(str(e))


def query_errors(
    page: int = 1,
    page_size: int = 50,
    level: str | None = None,
    source: str | None = None,
    q: str | None = None,
    since_hours: int | None = None,
) -> dict:
    try:
        sb = get_supabase()
        qry = sb.table("error_logs").select("*", count="exact")
        if level:
            qry = qry.eq("level", level)
        if source:
            qry = qry.ilike("source", f"%{source}%")
        if q:
            qry = qry.ilike("message", f"%{escape_like(q)}%")
        if since_hours:
            qry = qry.gte("created_at", cutoff_iso(since_hours))
        # pagination
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        start = (page - 1) * page_size
        qry = qry.order("created_at", desc=True).limit(page_size).offset(start)
        res = qry.execute()
        total = res.count or 0
        total_pages = (total + page_size - 1) // page_size if page_size else 1
        return {
            "results": res.data or [],
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
            "hasMore": page * page_size < total,
        }
    except Exception as e:
        logger.warn("query_errors failed", err=str(e)[:200])
        return {"results": [], "total": 0, "page": 1, "pageSize": page_size, "totalPages": 1, "hasMore": False}


def clear_all() -> int:
    try:
        res = get_supabase().table("error_logs").delete().neq("id", 0).execute()
        return len(res.data or [])
    except Exception as e:
        logger.warn("clear_all failed", err=str(e)[:200])
        return 0
def delete_older_than(days: int = 30) -> int:
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        res = get_supabase().table("error_logs").delete().lt("created_at", cutoff).execute()
        return len(res.data or [])
    except Exception as e:
        logger.warn("delete_older_than failed", err=str(e)[:200])
        return 0
