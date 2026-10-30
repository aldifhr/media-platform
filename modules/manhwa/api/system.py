"""System endpoints: cron trigger, cleanup (retention), metrics."""
import threading
import time as _time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.manhwa.logger import get_logger
from modules.manhwa.utils.request_auth import require_cron_auth, require_monitor_auth
from modules.manhwa.utils.responses import err, ok, unauthorized
from modules.manhwa.config import CRON_ACTIONS
from modules.manhwa.services.audit import log_action, AuditAction

logger = get_logger("api:system")
router = APIRouter()

# Cron concurrency guard: per-action lock + DB advisory lock (cross-process).
import collections
_cron_locks: dict[str, threading.Lock] = collections.defaultdict(threading.Lock)  # type: ignore[assignment]
# preload known actions so introspection still works
for _k in CRON_ACTIONS:
    _cron_locks[_k]  # touch

def get_cron_lock(action: str) -> threading.Lock:
    """Expose the per-action cron lock so other modules (e.g. dispatches'
    retry-all) can reuse the SAME lock and avoid double-running update."""
    return _cron_locks.get(action, _cron_locks["update"])

import hashlib as _hl

_cron_running = False
def _advisory_key(action: str) -> int:
    # Deterministic across processes (hash() is per-process seeded)
    return int(_hl.sha256(action.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF

_CRON_ADVISORY_KEY = 424242  # legacy fallback (not used directly, kept for compat)

# Lightweight in-memory cron job registry so callers can poll run status
# instead of inferring it from the cronStatus timestamp. Last N jobs kept.
_cron_jobs: list[dict] = []
_CRON_JOBS_MAX = 20

def _record_job(action: str, status: str, stats: dict | None = None):
    _cron_jobs.insert(0, {
        "action": action,
        "status": status,
        "started_at": _time.time(),
        "stats": stats,
    })
    while len(_cron_jobs) > _CRON_JOBS_MAX:
        _cron_jobs.pop()

@router.get("/cron")
@router.post("/cron")
async def cron_trigger(request: Request):
    # Strict trust boundary: cron is CRON_SECRET only (dashboard fallback removed — CSRF+JWT fallback is mitigated but separate boundary is cleaner; FE proxy injects ?token=CRON_SECRET server-side)
    if not require_cron_auth(request):
        return unauthorized()
    action = request.query_params.get("action", "update")
    source = request.query_params.get("source")
    
    # Build action string: "rss-fetch:shinigami"
    if source and action == "rss-fetch":
        action = f"rss-fetch:{source}"
    
    valid_actions = ("update", "rss-fetch", "health", "dispatch", "sync-meta", "enrich", "enrich-missing", "enrich-refresh")
    valid_source_actions = ("rss-fetch:shinigami", "rss-fetch:ikiru")
    
    if action not in valid_actions and action not in valid_source_actions:
        return err(f"unknown action: {action}", status_code=400)
    # Decoupled: enqueue to Redis cron queue; the ROLE=cron worker executes.
    # Keeps the slow scrape/dispatch off the HTTP path. If Redis is down, the
    # API returns 503 (Service Unavailable) instead of running inline — running
    # a 60-90s scrape inline would block the HTTP thread and 502 other users.
    # The ROLE=cron worker still falls back inline, so cron survives Redis outages.
    from modules.manhwa.tasks import enqueue_cron
    from modules.manhwa.config import settings as _cron_s
    try:
        enqueue_cron(action)
        try:
            log_action(AuditAction.CRON_TRIGGER, request=request, resource="cron", resource_id=action, metadata={"action": action, "source": source or ""})
        except Exception:
            pass
    except Exception:
        # local dev tanpa Redis (REDIS_URL="" di .env) -> fallback inline biar gak 503, VPS prod tetap 503
        if (getattr(_cron_s, "ENVIRONMENT", "production").lower() != "production" and not getattr(_cron_s, "REDIS_URL", "")):
            try:
                from modules.manhwa.cron.pipeline import run_pipeline
                # rss-fetch:* -> do_dispatch False, lain -> True
                _do_disp = not action.startswith("rss-fetch")
                stats = run_pipeline(do_dispatch=_do_disp, action=action)
                return ok({"status": "completed (inline dev)", "action": action, "stats": stats})
            except Exception as ie:
                return err(f"inline cron failed: {ie!s:.120}", status_code=500)
        return err("cron queue unavailable", status_code=503)
    return ok({"status": "enqueued", "action": action}, status_code=202)

@router.get("/metrics")
async def metrics(request: Request):
    # Accepts either cron or monitor auth (unified JSON metrics endpoint).
    from modules.manhwa.utils.request_auth import require_monitor_auth
    if not (require_cron_auth(request) or require_monitor_auth(request)):
        return unauthorized()
    try:
        from modules.manhwa.db import get_supabase
        from modules.manhwa.metrics import snapshot as _snap
        sb = get_supabase()
        rc = sb.table("recent_chapters").select("id", count="exact").limit(1).execute()
        wl = sb.table("whitelist").select("id", count="exact").limit(1).execute()
        dh = sb.table("dispatch_history").select("id", count="exact").limit(1).execute()
        return JSONResponse(content={
            "success": True,
            "data": {
                "recent_chapters_count": rc.count or 0,
                "whitelist_count": wl.count or 0,
                "dispatch_history_count": dh.count or 0,
                "counters": _snap().get("counters", {}),
            },
        })
    except Exception as e:
        logger.warn("cleanup failed", err=str(e)[:160])
        return err("internal error", status_code=500)


@router.get("/watchdog")
async def watchdog_status(request: Request):
    """Dispatch-lag health: whitelisted chapters inside the collect window
    that never made it into dispatch_history, plus queue/claim state.

    Read-only. Does NOT notify, so the dashboard can poll it freely.
    """
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        from modules.manhwa.cron.watchdog import snapshot as _wd_snapshot

        raw = request.query_params.get("window")
        window = 24
        if raw:
            try:
                window = max(1, min(168, int(raw)))
            except ValueError:
                return err("invalid window", status_code=400)

        data = _wd_snapshot(window_hours=window)
        # stalled is the signal; surface a status the UI can colour-code on.
        data["status"] = "error" if not data.get("ok") else (
            "critical" if data.get("urgent", 0) > 0
            else "warning" if data.get("stalled", 0) > 0
            else "healthy"
        )
        return ok(data)
    except Exception as e:
        logger.warn("watchdog endpoint failed", err=str(e)[:160])
        return err("internal error", status_code=500)
