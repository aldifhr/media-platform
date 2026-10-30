"""Queue dashboard — Redis queue monitoring for admin insight."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.manhwa.logger import get_logger
from modules.manhwa.utils.request_auth import require_monitor_auth
from modules.manhwa.utils.responses import err, ok, unauthorized
from modules.manhwa.services.audit import log_action, AuditAction

logger = get_logger("api:queue_dashboard")
router = APIRouter()


@router.get("/queue/cron")
async def cron_list(request: Request):
    """Full cron job list from Redis queue."""
    if not require_monitor_auth(request):
        return unauthorized()
    
    try:
        from modules.manhwa.tasks import _get_redis, CRON_QUEUE_KEY
        
        r = _get_redis()
        jobs_raw = r.lrange(CRON_QUEUE_KEY, 0, -1) or []
        
        import json
        jobs = []
        for j in jobs_raw:
            try:
                data = json.loads(j)
                jobs.append({
                    "action": data.get("action", "unknown"),
                    "kind": data.get("kind", "unknown"),
                    "title": (data.get("title") or "")[:60],
                    "attempts": data.get("attempts", 0),
                    "source": data.get("source", ""),
                })
            except Exception:
                jobs.append({"raw": j[:100]})
        
        return JSONResponse(content={
            "success": True,
            "data": {
                "total": len(jobs),
                "jobs": jobs,
            }
        })
    except Exception as e:
        logger.warn("cron_list failed", err=str(e)[:120])
        return err("internal error", status_code=500)


@router.get("/queue/status")
async def queue_status(request: Request):
    """Redis queue status — pending jobs, breakdown, pending chapters."""
    if not require_monitor_auth(request):
        return unauthorized()
    
    try:
        from modules.manhwa.tasks import _get_redis, QUEUE_KEY, CRON_QUEUE_KEY
        
        r = _get_redis()
        
        # Queue depths
        main_queue = r.llen(QUEUE_KEY) or 0
        cron_queue = r.llen(CRON_QUEUE_KEY) or 0
        
        # Peek at first 5 jobs in each queue
        main_jobs = r.lrange(QUEUE_KEY, 0, 4) or []
        cron_jobs = r.lrange(CRON_QUEUE_KEY, 0, 4) or []
        
        # Parse job info + breakdown
        import json
        def parse_jobs(raw_jobs):
            result = []
            for j in raw_jobs:
                try:
                    data = json.loads(j)
                    result.append({
                        "kind": data.get("kind", "unknown"),
                        "title": data.get("title", data.get("action", "unknown"))[:50],
                        "attempts": data.get("attempts", 0),
                    })
                except Exception:
                    result.append({"raw": j[:100]})
            return result
        
        # Breakdown cron jobs by action
        cron_breakdown: dict[str, int] = {}
        try:
            all_cron = r.lrange(CRON_QUEUE_KEY, 0, -1) or []
            for j in all_cron:
                try:
                    data = json.loads(j)
                    action = data.get("action", "unknown")
                    # Normalize: rss-fetch:shinigami → rss-fetch
                    key = action.split(":")[0] if ":" in action else action
                    cron_breakdown[key] = cron_breakdown.get(key, 0) + 1
                except Exception:
                    cron_breakdown["unknown"] = cron_breakdown.get("unknown", 0) + 1
        except Exception:
            pass
        
        # Pending dispatch chapters (whitelisted, unsent)
        pending_chapters = []
        try:
            from modules.manhwa.db import get_supabase
            from modules.manhwa.cron.dispatch_mod import fcfs_key as _fk, _claimed_titles
            from modules.manhwa.core.text import normalize_title_key as _ntk
            from modules.manhwa.storage import whitelist as wl_store
            from datetime import datetime, timezone, timedelta
            
            sb = get_supabase()
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            rc = sb.table("recent_chapters").select("id,title_key,source,chapter,chapter_num,title,chapter_url").gte("updated_time", cutoff).execute().data or []
            wl = wl_store.load_whitelist()
            wl_keys = {(_ntk(str(w.get("title_key", ""))), w.get("source", "")) for w in wl}
            whitelisted = [c for c in rc if (_ntk(str(c.get("title_key", ""))), c.get("source", "")) in wl_keys]
            ceil = {}
            for w in wl:
                tk = _ntk(str(w.get("title_key", "")))
                src = w.get("source", "")
                try:
                    ls = float(w.get("latest_sent_chapter") or 0)
                except Exception:
                    ls = 0
                if tk:
                    ceil[(tk, src)] = max(ceil.get((tk, src), 0), ls)
            pending = []
            for c in whitelisted:
                tk = _ntk(str(c.get("title_key", "")))
                src = c.get("source", "")
                c_ceil = ceil.get((tk, src), ceil.get((tk, ""), 0))
                try:
                    ch = float(c.get("chapter_num") or c.get("chapter") or 0)
                except Exception:
                    ch = 0
                if c_ceil and ch <= c_ceil:
                    continue
                pending.append(c)
            keys = [_fk(c.get("title", ""), c.get("chapter", "")) for c in pending]
            claimed = _claimed_titles(list(set(keys))) if keys else set()
            final = [c for i, c in enumerate(pending) if keys[i] not in claimed]
            pending_chapters = [
                {
                    "id": c.get("id"),
                    "title": c.get("title", ""),
                    "title_key": c.get("title_key", ""),
                    "chapter": c.get("chapter", ""),
                    "chapter_num": float(c.get("chapter_num") or 0),
                    "source": c.get("source", ""),
                    "chapter_url": c.get("chapter_url", ""),
                }
                for c in final[:50]
            ]
        except Exception as e:
            logger.warn("pending_chapters failed", err=str(e)[:120])
        
        return JSONResponse(content={
            "success": True,
            "data": {
                "depth": main_queue + cron_queue,
                "depths": {
                    "main_queue": main_queue,
                    "cron_queue": cron_queue,
                },
                "pending_jobs": {
                    "main": parse_jobs(main_jobs),
                    "cron": parse_jobs(cron_jobs),
                },
                "cron_breakdown": cron_breakdown,
                "pending_chapters": pending_chapters,
            }
        })
    except Exception as e:
        logger.warn("queue_status failed", err=str(e)[:120])
        return err("internal error", status_code=500)


@router.post("/queue/retry-dlq")
async def retry_dlq(request: Request):
    """Move all DLQ jobs back to main queue for retry."""
    if not require_monitor_auth(request):
        return unauthorized()
    
    try:
        from modules.manhwa.tasks import _get_redis, QUEUE_KEY, DLQ_KEY
        
        r = _get_redis()
        count = 0
        while True:
            job = r.rpoplpush(DLQ_KEY, QUEUE_KEY)
            if not job:
                break
            count += 1
        try:
            log_action(AuditAction.QUEUE_RETRY, request=request, resource="queue", resource_id="dlq", metadata={"retried": count})
        except Exception:
            pass
        
        return ok({"retried": count})
    except Exception as e:
        logger.warn("retry_dlq failed", err=str(e)[:120])
        return err("internal error", status_code=500)


@router.delete("/queue/pending")
async def clear_pending(request: Request):
    """Mark all pending chapters as dispatched (add to dispatch_history)."""
    if not require_monitor_auth(request):
        return unauthorized()
    
    try:
        from modules.manhwa.db import get_supabase
        from modules.manhwa.cron.dispatch_mod import fcfs_key as _fk, _claimed_titles
        from modules.manhwa.core.text import normalize_title_key as _ntk
        from modules.manhwa.storage import whitelist as wl_store
        from datetime import datetime, timezone, timedelta
        
        sb = get_supabase()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        rc = sb.table("recent_chapters").select("id,title_key,source,chapter,chapter_num,title,chapter_url").gte("updated_time", cutoff).execute().data or []
        wl = wl_store.load_whitelist()
        wl_keys = {(_ntk(str(w.get("title_key", ""))), w.get("source", "")) for w in wl}
        whitelisted = [c for c in rc if (_ntk(str(c.get("title_key", ""))), c.get("source", "")) in wl_keys]
        ceil = {}
        for w in wl:
            tk = _ntk(str(w.get("title_key", "")))
            src = w.get("source", "")
            try:
                ls = float(w.get("latest_sent_chapter") or 0)
            except Exception:
                ls = 0
            if tk:
                ceil[(tk, src)] = max(ceil.get((tk, src), 0), ls)
        pending = []
        for c in whitelisted:
            tk = _ntk(str(c.get("title_key", "")))
            src = c.get("source", "")
            c_ceil = ceil.get((tk, src), ceil.get((tk, ""), 0))
            try:
                ch = float(c.get("chapter_num") or c.get("chapter") or 0)
            except Exception:
                ch = 0
            if c_ceil and ch <= c_ceil:
                continue
            pending.append(c)
        keys = [_fk(c.get("title", ""), c.get("chapter", "")) for c in pending]
        claimed = _claimed_titles(list(set(keys))) if keys else set()
        final = [c for i, c in enumerate(pending) if keys[i] not in claimed]
        
        # Mark as dispatched by adding to dispatch_history
        now = datetime.now(timezone.utc).isoformat()
        history_rows = []
        for c in final:
            history_rows.append({
                "title_key": _ntk(str(c.get("title_key", ""))),
                "title": c.get("title", ""),
                "chapter_title": c.get("chapter", ""),
                "source": c.get("source", ""),
                "chapter_url": c.get("chapter_url", ""),
                "created_at": now,
            })
        if history_rows:
            sb.table("dispatch_history").insert(history_rows).execute()
        try:
            log_action(AuditAction.QUEUE_CLEAR, request=request, resource="queue", resource_id="pending", metadata={"cleared": len(history_rows)})
        except Exception:
            pass
        
        return ok({"cleared": len(history_rows)})
    except Exception as e:
        logger.warn("clear_pending failed", err=str(e)[:120])
        return err("internal error", status_code=500)


@router.delete("/queue/cron")
async def clear_cron_queue(request: Request):
    """Clear all jobs from the cron queue."""
    if not require_monitor_auth(request):
        return unauthorized()

    try:
        from modules.manhwa.tasks import _get_redis, CRON_QUEUE_KEY, CRON_QUEUE_SET, CRON_PROCESSING_KEY

        r = _get_redis()
        count = r.llen(CRON_QUEUE_KEY) or 0
        r.delete(CRON_QUEUE_KEY)
        try:
            r.delete(CRON_QUEUE_SET)
            r.delete(CRON_PROCESSING_KEY)
        except Exception:
            pass
        try:
            log_action(AuditAction.QUEUE_CLEAR, request=request, resource="queue", resource_id="cron", metadata={"deleted": count})
        except Exception:
            pass

        return ok({"deleted": count})
    except Exception as e:
        logger.warn("clear_cron_queue failed", err=str(e)[:120])
        return err("internal error", status_code=500)


@router.delete("/queue/dlq")
async def clear_dlq(request: Request):
    """Clear all jobs from dead letter queue."""
    if not require_monitor_auth(request):
        return unauthorized()
    
    try:
        from modules.manhwa.tasks import _get_redis, DLQ_KEY
        
        r = _get_redis()
        count = r.llen(DLQ_KEY) or 0
        r.delete(DLQ_KEY)
        try:
            log_action(AuditAction.QUEUE_CLEAR, request=request, resource="queue", resource_id="dlq", metadata={"deleted": count})
        except Exception:
            pass
        
        return ok({"deleted": count})
    except Exception as e:
        logger.warn("clear_dlq failed", err=str(e)[:120])
        return err("internal error", status_code=500)
