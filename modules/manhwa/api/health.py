"""Health endpoints — extracted from main.py god-file."""
import time as _time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.manhwa.logger import get_logger
from modules.manhwa.utils.request_auth import safe_error, require_monitor_auth
from modules.manhwa.utils.responses import ok, unauthorized

APP_START_TS = _time.time()


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"

logger = get_logger("api:health")
router = APIRouter()


@router.get("/healthz")
async def healthz():
    """Liveness probe: check DB + Redis are reachable."""
    try:
        from modules.manhwa.db import q as _q
        _q("SELECT 1")
    except Exception as e:
        logger.warn("healthz db check failed", err=str(e)[:200])
        return JSONResponse(content={"status": "error", "service": "be-ag-py", "error": "unavailable"}, status_code=503)
    try:
        from modules.manhwa.tasks.queue import _get_redis as _gr
        _gr().ping()
    except Exception as e:
        logger.warn("healthz redis check failed", err=str(e)[:200])
        return JSONResponse(content={"status": "error", "service": "be-ag-py", "error": "unavailable"}, status_code=503)
    return {"status": "ok", "service": "be-ag-py"}


@router.get("/health")
async def api_health(request: Request):
    """Health + source status overview (monitor auth)."""
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        from modules.manhwa.config import settings as _s
        from modules.manhwa.storage import health as _h
        from modules.manhwa.db import get_supabase as _gsb

        hm = _h.load_source_health_map(_s.SOURCE_KEYS)
        sources = []
        # Disabled sources are operationally absent from the tracker.
        for src, row in (hm or {}).items():
            if src not in _s.active_sources:
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
                "disabledUntil": row.get("disabled_until"),
            })

        pending = 0
        try:
            from modules.manhwa.storage import recent_chapters as _rc
            rows = _rc._fetch_recent_rows(hours=24, limit=2000, offset=0)
            urls = [r.get("chapter_url") for r in rows if r.get("chapter_url")]
            if urls:
                _dh = _gsb().table("dispatch_history").select("chapter_url").in_("chapter_url", urls).execute()
                sent_urls = {r["chapter_url"] for r in (_dh.data or [])}
                pending = sum(1 for u in urls if u not in sent_urls)
        except Exception:
            pending = -1

        return JSONResponse(content={
            "success": True,
            "data": {
                "sources": sources,
                "pending": pending,
                "service": "be-ag-py",
                "status": "healthy",
            },
        })
    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)


@router.get("/health/detailed")
async def health_detailed(request: Request):
    """Detailed health — circuit breakers + pool stats."""
    if not require_monitor_auth(request):
        return unauthorized()
    from modules.manhwa.core.resilience import cb_discord, cb_db, cb_shinigami
    from modules.manhwa.db import get_pool_stats
    from modules.manhwa.storage import health as health_store
    from modules.manhwa.config import settings
    try:
        pool = get_pool_stats()
    except Exception:
        pool = {"active": -1, "idle": -1}
    hm = health_store.load_source_health_map(settings.SOURCE_KEYS)
    sources = []
    # Add disabled sources
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
            "disabledUntil": row.get("disabled_until"),
        })
    overall = "healthy"
    # source_health.status is stored UPPERCASE ("HEALTHY"/"DEGRADED"/"DOWN"),
    # so comparing against lowercase literals never matched and a source that
    # was actually DOWN still rolled up to overall "healthy". Normalise first.
    down_count = sum(1 for s in sources if str(s["status"] or "").lower() == "down")
    degraded_count = sum(1 for s in sources if str(s["status"] or "").lower() == "degraded")
    if down_count > 0:
        overall = "down"
    elif degraded_count > 0:
        overall = "degraded"
    return {
        "success": True,
        "data": {
            "sources": sources,
            "overall": overall,
            "uptime": 100.0,
            "uptime_formatted": _fmt_uptime(_time.time() - APP_START_TS),
            "version": "1.1.0",
            "pool": pool,
            "circuit_breakers": {
                "discord": cb_discord.state.value,
                "db": cb_db.state.value,
                "shinigami": cb_shinigami.state.value,
            },
        },
    }


@router.get("/health-status")
async def health_status(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    from modules.manhwa.db import get_supabase
    from modules.manhwa.storage import health as health_store
    import time as _t

    from modules.manhwa.config import settings as _s
    hm = health_store.load_source_health_map(_s.SOURCE_KEYS) or {}
    for _sk in _s.SOURCE_KEYS:
        if _sk not in hm:
            hm[_sk] = {"source": _sk, "status": "unknown", "consecutive_failures": 0, "last_error": "no data yet", "last_success_at": None, "last_checked_at": None, "response_time_ms": None}
    _api_ping = None
    for r in hm.values():
        rt = r.get("response_time_ms") or 0
        if rt and (_api_ping is None or rt < _api_ping):
            _api_ping = rt
    _sb_ping = None
    _disc_ping = None
    try:
        _sb_client = get_supabase()
        _t0 = _t.time()
        _sb_client.table("source_health").select("source").limit(1).execute()
        _sb_ping = int((_t.time() - _t0) * 1000)
    except Exception:
        _sb_ping = None
    try:
        from modules.manhwa.discord import client as _disc
        import httpx as _httpx
        _dt0 = _t.time()
        _tok = getattr(_disc, "TOKEN", None) or _s.DISCORD_BOT_TOKEN
        if _tok:
            with _httpx.Client(timeout=5) as _cx:
                _cx.get("https://discord.com/api/v10/gateway", headers={"Authorization": f"Bot {_tok}"})
            _disc_ping = int((_t.time() - _dt0) * 1000)
    except Exception:
        _disc_ping = None
    services = []
    for name in ("api", "discord", "supabase"):
        if name == "api":
            status = "healthy"
            ping = f"{_api_ping}ms" if _api_ping else None
        elif name == "discord":
            disc_ok = _disc_ping is not None
            status = "healthy" if disc_ok else "degraded"
            ping = f"{_disc_ping}ms" if _disc_ping else None
        else:
            status = "healthy" if _sb_ping is not None else "degraded"
            ping = f"{_sb_ping}ms" if _sb_ping else None
        services.append({"name": name, "status": status, "ping": ping, "uptime": _fmt_uptime(_t.time() - APP_START_TS) if name == "api" else None})
    _has_degraded = any(s["status"] == "degraded" for s in services)
    # hm holds the RAW source_health rows, whose status is UPPERCASE. Same trap
    # as the roll-up above: a DOWN source left the overall at "healthy".
    _has_down = any(str(r.get("status") or "").lower() == "down" for r in (hm or {}).values())
    _overall = "down" if _has_down else ("degraded" if _has_degraded else "healthy")
    return ok({"services": services, "uptime": _fmt_uptime(_t.time() - APP_START_TS), "sources": hm or {}, "status": _overall})


@router.get("/internal/metrics")
async def internal_metrics(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    from modules.manhwa.db import get_supabase

    try:
        sb = get_supabase()

        def _count(table: str) -> int:
            try:
                return sb.table(table).select("*", count="exact").execute().count or 0
            except Exception:
                return -1

        data = {
            "whitelist": _count("whitelist"),
            "recent_chapters": _count("recent_chapters"),
            "dispatch_history": _count("dispatch_history"),
            "failed_dispatches": _count("failed_dispatches"),
            "cron_run_status": _count("cron_run_status"),
            "source_health": _count("source_health"),
        }
        try:
            from modules.manhwa.metrics import snapshot as _snap
            data["counters"] = _snap().get("counters", {})
        except Exception:
            pass
        return ok(data)
    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)
