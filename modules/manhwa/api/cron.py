"""Cron — single domain: status + health. Consolidates cron_status + cron_health."""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request

from modules.manhwa.logger import get_logger
from modules.manhwa.utils.request_auth import safe_error, require_monitor_auth, scrub_upstream_errors
from modules.manhwa.utils.responses import unauthorized
from modules.manhwa.tasks import get_cron_status
from modules.manhwa.config import settings

logger = get_logger("api:cron")
router = APIRouter()

_CRON_WORKER_URL = f"http://127.0.0.1:{settings.CRON_PORT}/api/v1/cron/status"


@router.get("/cron/status")
async def cron_status(request: Request):
    try:
        local = get_cron_status()
        if local.get("scheduler_alive"):
            return scrub_upstream_errors(local, authenticated=require_monitor_auth(request))
    except Exception:
        local = None
    try:
        with urllib.request.urlopen(_CRON_WORKER_URL, timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        if local is not None:
            local["scheduler_alive"] = False
            local["worker_reachable"] = False
            return scrub_upstream_errors(local, authenticated=require_monitor_auth(request))
        # The exception text names the worker URL (127.0.0.1:<port>) and the
        # failure mode. Authenticated operators get it; anonymous callers get
        # a generic "unavailable" so the status page still renders.
        _detail = f"cron status unavailable: {e}" if require_monitor_auth(request) else "cron status unavailable"
        return safe_error(e, _detail)


@router.get("/cron/health")
async def cron_health(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    now = datetime.now(timezone.utc)
    sources = []
    try:
        from modules.manhwa.storage import health as health_store
        hm = health_store.load_source_health_map(settings.SOURCE_KEYS)
        sources = []
        # Add disabled sources first
        for src in settings.SOURCE_KEYS:
            if src not in settings.active_sources:
                sources.append({
                    "name": src,
                    "status": "disabled",
                    "lastScrape": "",
                    "lastSuccess": "",
                    "errorRate24h": 0.0,
                    "consecutiveFailures": 0,
                    "lastError": "disabled via DISABLED_SOURCES",
                    "responseTimeMs": 0,
                    "disabledUntil": None,
                })
        for src, row in (hm or {}).items():
            if src not in settings.active_sources:
                continue
            ok_24h = int(row.get("successes_today") or 0) + int(row.get("failures_today") or 0)
            err_rate = round(100.0 * int(row.get("failures_today") or 0) / ok_24h, 1) if ok_24h else 0.0
            sources.append({
                "name": src,
                "status": row.get("status", "healthy"),
                "lastScrape": row.get("last_checked_at") or "",
                "lastSuccess": row.get("last_success_at") or "",
                "errorRate24h": err_rate,
                "consecutiveFailures": row.get("consecutive_failures") or 0,
                "lastError": row.get("last_error"),
                "responseTimeMs": row.get("response_time_ms", 0),
                "disabledUntil": row.get("disabled_until"),
            })
    except Exception as e:
        logger.warn("cron-health: source health failed", err=str(e)[:120])
    dispatch_stats = {"sent_24h": 0, "failed_24h": 0, "pending": 0, "total": 0}
    try:
        from modules.manhwa.db import get_supabase
        sb = get_supabase()
        sent_24h = sb.table("dispatch_history").select("id").gte("sent_at", (now - timedelta(hours=24)).isoformat()).execute()
        dispatch_stats["sent_24h"] = len(sent_24h.data or [])
        total = sb.table("dispatch_history").select("id").execute()
        dispatch_stats["total"] = len(total.data or [])
    except Exception as e:
        logger.warn("cron-health: dispatch stats failed", err=str(e)[:120])
    failed = []
    try:
        from modules.manhwa.db import get_supabase
        sb = get_supabase()
        failed_rows = sb.table("failed_dispatches").select("*").order("created_at", desc=True).limit(10).execute()
        for r in (failed_rows.data or []):
            failed.append({"id": r.get("id"), "title_key": r.get("title_key"), "source": r.get("source"), "chapter": r.get("chapter_title"), "error": (r.get("error_message") or "")[:100], "createdAt": r.get("created_at")})
    except Exception:
        pass
    queue_depth = 0
    queue_breakdown = {}
    try:
        from modules.manhwa.tasks.queue import _get_redis, QUEUE_KEY, CRON_QUEUE_KEY, QUEUE_PROCESSING_KEY, CRON_PROCESSING_KEY
        redis = _get_redis()
        queue_depth = sum(redis.llen(k) for k in (QUEUE_KEY, CRON_QUEUE_KEY, QUEUE_PROCESSING_KEY, CRON_PROCESSING_KEY))
        queue_breakdown = {"main": redis.llen(QUEUE_KEY), "cron": redis.llen(CRON_QUEUE_KEY), "processing": redis.llen(QUEUE_PROCESSING_KEY) + redis.llen(CRON_PROCESSING_KEY)}
    except Exception:
        pass
    scheduler = {"alive": False, "last_run": None, "next_run": None}
    try:
        ss = get_cron_status()
        scheduler["alive"] = ss.get("scheduler_alive", False)
        scheduler["last_run"] = ss.get("last_run")
        scheduler["next_run"] = ss.get("next_run")
    except Exception:
        pass
    # ROLE=api has no scheduler thread of its own (main.py only starts it when
    # ROLE=cron), so the local snapshot is always alive=False there. Ask the
    # cron worker directly before reporting the pipeline as down — otherwise
    # /cron/health claims the scheduler is dead while dispatch runs every 120s.
    if not scheduler["alive"]:
        try:
            req = urllib.request.Request(
                _CRON_WORKER_URL,
                headers={"Authorization": f"Bearer {settings.MONITOR_AUTH_TOKEN}"},
            )
            with urllib.request.urlopen(req, timeout=3) as _r:
                _remote = json.loads(_r.read().decode())
            if _remote.get("scheduler_alive"):
                scheduler["alive"] = True
                scheduler["last_run"] = _remote.get("last_run")
                scheduler["next_run"] = _remote.get("next_run")
        except Exception:
            pass
    circuits = {}
    try:
        from modules.manhwa.core.resilience import cb_discord, cb_db, cb_shinigami
        for name, cb in [("discord", cb_discord), ("db", cb_db), ("shinigami", cb_shinigami)]:
            circuits[name] = cb.state.value
    except Exception:
        pass
    return {"success": True, "data": {"sources": sources, "dispatch": dispatch_stats, "failed": failed, "queue": {"depth": queue_depth, "breakdown": queue_breakdown}, "scheduler": scheduler, "circuits": circuits, "timestamp": now.isoformat()}}
