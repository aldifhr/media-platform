"""Auto-split from dashboard.py — dashboard health/stats routes."""

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.manhwa.logger import get_logger
from modules.manhwa.services.dashboard.snapshot import (  # noqa: F401
    _SNAP_CACHE,
    _SNAP_TTL,
    _build_snapshot,
    build_snapshot_sync,
)
from modules.manhwa.services.health_service import (  # noqa: F401
    load_source_health_map,
    read_dashboard_snapshot,
)
from modules.manhwa.utils.request_auth import require_monitor_auth, scrub_upstream_errors

# Cache for /sources/health (30s TTL).
_SRC_HEALTH_CACHE: list = [0.0, None]  # [ts, payload]
_SRC_HEALTH_TTL = 30.0

logger = get_logger("api:stats")
router = APIRouter()


@router.get("/sources/health")
async def sources_health(request: Request):
    _now = time.monotonic()
    if _SRC_HEALTH_CACHE[0] is not None and (_now - _SRC_HEALTH_CACHE[0]) < _SRC_HEALTH_TTL:
        return JSONResponse(
            content=scrub_upstream_errors(
                _SRC_HEALTH_CACHE[1], authenticated=require_monitor_auth(request)
            ),
            headers={
                "Cache-Control": "private, max-age=30, stale-while-revalidate=60",
                "Vary": "Cookie",
            },
        )
    from modules.manhwa.config import settings as s

    hm = load_source_health_map(s.SOURCE_KEYS)
    # This endpoint is public (the status page is anonymous), so raw upstream
    # error text is stripped unless the caller authenticated.
    _authed = require_monitor_auth(request)
    # Return a flat ARRAY (not {results: dict}) so the frontend's
    # /api/reader/sources/health proxy can pass body.data straight to .map()
    #
    # Disabled sources are omitted entirely. A disabled source has no health
    # row of its own to report — it is not failing, it is switched off — and
    # listing it as "healthy" contradicts DISABLED_SOURCES and the frontend's
    # own source list (ALL_SOURCES), which already excludes it. The frontend
    # drove this list, so leaving a source here showed a status row for
    # something the UI otherwise pretends does not exist.
    _active = set(s.active_sources)
    results = []
    for src, row in (hm or {}).items():
        if src not in _active:
            continue
        results.append(
            {
                "name": src,
                "source": src,
                "status": row.get("status", "healthy"),
                "lastCheck": row.get("last_checked_at") or "",
                "lastSuccess": row.get("last_success_at") or "",
                "avgResponseTimeMs": row.get("response_time_ms") or 0,
                "consecutiveFailures": row.get("consecutive_failures") or 0,
                "lastError": row.get("last_error"),
                "disabledUntil": row.get("disabled_until"),
                "successesToday": row.get("successes_today") or 0,
                "failuresToday": row.get("failures_today") or 0,
            }
        )
    payload = {"success": True, "data": results}
    # Cache the RAW payload and scrub on the way out: caching the scrubbed
    # variant would either leak to the next anonymous caller or starve the
    # next authenticated one, depending on who populated the 30s window.
    _SRC_HEALTH_CACHE[0] = time.monotonic()
    _SRC_HEALTH_CACHE[1] = payload
    return JSONResponse(
        content=scrub_upstream_errors(payload, authenticated=_authed),
        headers={
            "Cache-Control": "private, max-age=30, stale-while-revalidate=60",
            "Vary": "Cookie",
        },
    )


@router.get("/dashboard-snapshot")
async def dashboard_snapshot(request: Request):
    # 15s in-memory cache — frontend polls every 30-60s, so this absorbs
    # duplicate bursts and cuts Supabase query volume by ~60%.
    _authed = require_monitor_auth(request)
    _hdrs = {"Cache-Control": "private, max-age=30, stale-while-revalidate=60", "Vary": "Cookie"}
    now = time.monotonic()
    if _SNAP_CACHE[0] and (now - _SNAP_CACHE[0]) < _SNAP_TTL:
        return JSONResponse(
            content=scrub_upstream_errors(_SNAP_CACHE[1], authenticated=_authed), headers=_hdrs
        )
    try:
        # Read the materialized singleton row (cron writes it every run).
        # ~20ms vs ~3s for the full recompute. Fallback to compute
        # if the row is missing (cron hasn't run yet / DB hiccup).
        _row = read_dashboard_snapshot()
        if _row and _row.get("payload"):
            payload = _row["payload"]
            _SNAP_CACHE[0] = time.monotonic()
            _SNAP_CACHE[1] = payload
            return JSONResponse(
                content=scrub_upstream_errors(payload, authenticated=_authed), headers=_hdrs
            )
        # Fallback: compute live (same as before).
        payload = await _build_snapshot()
        return JSONResponse(
            content=scrub_upstream_errors(payload, authenticated=_authed), headers=_hdrs
        )
    except Exception:
        # on error, return stale cache if available, else empty
        if _SNAP_CACHE[1] is not None:
            return JSONResponse(
                content=scrub_upstream_errors(_SNAP_CACHE[1], authenticated=_authed), headers=_hdrs
            )
        raise
