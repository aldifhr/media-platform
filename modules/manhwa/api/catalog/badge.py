"""Catalog badge counts — GET /catalog/badge-counts & /reader/badge-counts."""
import time as _qtime
from collections import OrderedDict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from modules.manhwa.logger import get_logger
from modules.manhwa.utils.request_auth import require_monitor_auth
from modules.manhwa.utils.responses import ok, unauthorized

logger = get_logger("api:catalog:badge")
router = APIRouter()

_BADGE_CACHE: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_BADGE_CACHE_MAX = 32


@router.get("/catalog/badge-counts")
@router.get("/reader/badge-counts")
async def badge_counts(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    from modules.manhwa.db import get_supabase
    _now = _qtime.time()
    _cached = _BADGE_CACHE.get("badge")
    if _cached is not None and (_now - _cached[0]) < 15:
        return JSONResponse(content=_cached[1], headers={"Cache-Control": "private, max-age=30"})
    try:
        sb = get_supabase()
        fd_res = sb.table("failed_dispatches").select("*", count="exact").eq("status", "failed").execute()
        failed = fd_res.count or 0
        payload = {"success": True, "data": {"activeIncidents": 0, "failedDispatches": failed, "unreadCount": 0}}
        _BADGE_CACHE["badge"] = (_qtime.time(), payload)
        if len(_BADGE_CACHE) > _BADGE_CACHE_MAX:
            _BADGE_CACHE.popitem(last=False)
        return JSONResponse(content=payload, headers={"Cache-Control": "private, max-age=30"})
    except Exception:
        return ok({"activeIncidents": 0, "failedDispatches": 0, "unreadCount": 0}, headers={"Cache-Control": "private, max-age=30"})
