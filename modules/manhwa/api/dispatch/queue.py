"""Pending dispatch queue depth — GET /failed-dispatches/queue.

Split out of api/dispatch/__init__.py.
"""
import time as _time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.manhwa.utils.request_auth import require_monitor_auth, safe_error
from modules.manhwa.utils.responses import unauthorized

router = APIRouter()

# --- Pending dispatch queue depth ---
_QDEPTH_CACHE: dict = {"ts": 0.0, "data": None}
_QDEPTH_TTL = 10  # seconds


@router.get("/failed-dispatches/queue")
async def failed_queue(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    from modules.manhwa.db import get_supabase
    from modules.manhwa.storage import whitelist as _wl
    from modules.manhwa.storage import dispatch as _ds
    from modules.manhwa.storage import recent_chapters as _rc
    from modules.manhwa.cron.collect import filter_whitelisted

    now = _time.time()
    if _QDEPTH_CACHE["data"] is not None and (now - _QDEPTH_CACHE["ts"]) < _QDEPTH_TTL:
        return JSONResponse(content=_QDEPTH_CACHE["data"])

    try:
        items = _rc.get_recent_chapters(hours=24)
        wl_rows = _wl.load_whitelist()
        to_dispatch = filter_whitelisted(items, wl_rows) if wl_rows else []
        # Load latest_sent ceiling per (title_key, source) so we skip chapters
        # already notified (mirrors the send-phase ceiling; otherwise stale
        # low-numbered chapters that were never recorded in dispatch_history
        # show up as "pending" forever).
        from modules.manhwa.core.text import normalize_title_key as _ntk
        _ceil: dict[tuple[str, str], float] = {}
        for w in (wl_rows or []):
            tk = _ntk(w.get("title_key", ""))
            src = w.get("source") or ""
            try:
                ls = float(w.get("latest_sent_chapter") or 0)
            except Exception:
                ls = 0.0
            if tk:
                _ceil[(_tk := tk, src)] = max(_ceil.get((tk, src), 0), ls)
        urls = [it.get("url") or it.get("chapter_url") for it in to_dispatch if it.get("url") or it.get("chapter_url")]
        claimed = set()
        if urls:
            try:
                claimed |= _ds._claimed_urls(urls)
            except Exception:
                pass
            try:
                dh = (
                    get_supabase()
                    .table("dispatch_history")
                    .select("chapter_url")
                    .in_("chapter_url", urls)
                    .execute()
                )
                claimed |= {r["chapter_url"] for r in (dh.data or [])}
            except Exception:
                pass
            # FCFS-aware: a chapter is "sent" if its (title, chapter) fcfs_key
            # is already in dispatch_history — even when the *URL* differs
            # (different sources publish the same chapter with different URLs;
            # the faster source wins FCFS and the slower one is correctly
            # skipped, but its distinct URL would otherwise look "pending" here).
            try:
                from modules.manhwa.cron.dispatch_mod import fcfs_key
                pending_keys = [
                    fcfs_key(it.get("title", ""), it.get("chapter", ""))
                    for it in to_dispatch
                    if (it.get("url") or it.get("chapter_url"))
                ]
                if pending_keys:
                    kh = (
                        get_supabase()
                        .table("dispatch_history")
                        .select("fcfs_key")
                        .in_("fcfs_key", pending_keys)
                        .execute()
                    )
                    claimed_keys = {r["fcfs_key"] for r in (kh.data or []) if r.get("fcfs_key")}
                    for it in to_dispatch:
                        u = it.get("url") or it.get("chapter_url")
                        if not u:
                            continue
                        k = fcfs_key(it.get("title", ""), it.get("chapter", ""))
                        if k in claimed_keys:
                            claimed.add(u)
            except Exception:
                pass
        pending = [u for u in urls if u not in claimed]
        # Apply latest_sent ceiling: chapters at/below the notified ceiling
        # are NOT pending (they were already sent / will be skipped by cron).
        ceiling_pending = []
        for it in to_dispatch:
            u = it.get("url") or it.get("chapter_url")
            if not u or u not in pending:
                continue
            tk = _ntk(it.get("title_key", ""))
            src = it.get("source") or ""
            ceil = _ceil.get((tk, src), _ceil.get((tk, ""), 0))
            try:
                ch = float(it.get("chapter_num") or it.get("chapter") or 0)
            except Exception:
                ch = 0.0
            if ceil and ch <= ceil:
                continue  # already sent (ceiling) -> not pending
            ceiling_pending.append(u)
        result = {
            "success": True,
            "data": {
                "depth": len(ceiling_pending),
                "queue": ceiling_pending[:50],
                "total_whitelisted": len(urls),
                "sent": len(urls) - len(ceiling_pending),
            },
        }
        _QDEPTH_CACHE["ts"] = now
        _QDEPTH_CACHE["data"] = result
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)
