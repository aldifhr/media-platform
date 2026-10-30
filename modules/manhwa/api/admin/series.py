"""Auto-split from dashboard.py — catalog routes."""
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from modules.manhwa.config import settings
from modules.manhwa.utils.request_auth import require_monitor_auth
from modules.manhwa.utils.responses import ok, unauthorized
from modules.manhwa.core.cover import cover_ref
from modules.manhwa.logger import get_logger
from modules.manhwa.services.whitelist_service import load_whitelist as _load_whitelist
from modules.manhwa.core.origin import normalize_origin
from modules.manhwa.core.text import normalize_title_key
logger = get_logger("api:catalog")
router = APIRouter()


@router.get("/catalog/search")
async def catalog_search(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    q = request.query_params.get("q", "")
    from modules.manhwa.sources import shinigami

    # Search the shinigami API for series.
    raw = await asyncio.to_thread(shinigami.search_shinigami_api, q, 10)
    for _r in raw:
        if not _r.get("source"):
            _perm = _r.get("permalink", "") or ""
            _r["source"] = "shinigami"
    from modules.manhwa.services.whitelist_service import load_whitelist as _load_wl
    _wl_keys = set()
    try:
        for _w in (_load_wl() or []):
            _tk = _w.get("title_key") or ""
            if _tk:
                _wl_keys.add(normalize_title_key(_tk))
    except Exception:
        pass
    results = []
    for r in raw:
        src = r.get("source") or ""
        title = r.get("title") or ""
        if not title:
            continue
        slug = r.get("slug") or r.get("permalink", "").rstrip("/").split("/")[-1] or normalize_title_key(title)
        _raw_origin = r.get("origin") or r.get("type") or []
        if isinstance(_raw_origin, list):
            _raw_origin = _raw_origin[0] if _raw_origin else ""
        _origin_cc = normalize_origin(_raw_origin)
        if not _origin_cc:
            _origin_cc = "JP" if src == "shinigami" else ""
        _is_wl = normalize_title_key(slug) in _wl_keys
        results.append({
            "title": title,
            "titleKey": slug,
            "cover": cover_ref(slug),
            "source": src,
            "url": r.get("permalink") or r.get("url") or r.get("series_url") or (f"{settings.SHINIGAMI_PUBLIC_BASE}/series/{r.get('manga_id')}" if src == "shinigami" and r.get("manga_id") else ""),
            "origin": _origin_cc,
            "isInWhitelist": _is_wl,
        })
    return ok({"results": results, "count": len(results)})


@router.get("/catalog/stats")
async def catalog_stats(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    rows = _load_whitelist() or []
    total = len(rows)
    status_distribution: dict[str, int] = {}
    source_distribution: dict[str, int] = {}
    rating_buckets = {"0-2": 0, "3-5": 0, "6-8": 0, "9-10": 0, "unrated": 0}
    total_with_rating = 0
    for r in rows:
        src = r.get("source") or "unknown"
        source_distribution[src] = source_distribution.get(src, 0) + 1
        rating = r.get("rating")
        if rating is None:
            rating_buckets["unrated"] += 1
        else:
            try:
                v = float(rating)
                total_with_rating += 1
                if v < 3:
                    rating_buckets["0-2"] += 1
                elif v < 6:
                    rating_buckets["3-5"] += 1
                elif v < 9:
                    rating_buckets["6-8"] += 1
                else:
                    rating_buckets["9-10"] += 1
            except Exception:
                rating_buckets["unrated"] += 1
    return JSONResponse(
        content={
            "success": True,
            "data": {
                "total": total,
                "statusDistribution": status_distribution,
                "sourceDistribution": source_distribution,
                "ratingBuckets": {"buckets": rating_buckets, "totalWithRating": total_with_rating},
            },
            "whitelist_count": total,
        }
    )
