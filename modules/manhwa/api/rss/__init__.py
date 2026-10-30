"""RSS feed — simplified architecture.

Data flow: recent_chapters (DB-filtered) → whitelist/series_meta/dispatch_history scoped to rc_tks (≤300) → Python map

Before: 6 full-table scans → 450ms + Python filter
After:  4 scoped queries (rc + wl IN rc_tks + sm IN rc_tks + dh IN rc_tks, ≤300 rows each), ~100ms. Next: single JOIN via v_series.
"""
import time as _time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from modules.manhwa.logger import get_logger
from modules.manhwa.utils.request_auth import int_safe, safe_error, require_monitor_auth
from modules.manhwa.utils.responses import err, ok, unauthorized
from modules.manhwa.services.rss_query import group_results

logger = get_logger("api:rss")
router = APIRouter()

from modules.manhwa.core.rss_cache import (  # noqa: F401
    _RSS_CACHE,
    _RSS_TTL,
    _rss_new_cache,
    invalidate_rss_cache,
    rss_cache_get as _rss_cache_get,
    rss_cache_put as _rss_cache_put,
)

@router.get("/rss")
async def rss(request: Request):
    # Public read-only feed (used by the manhwa.aldifhr.my.id /recent reader page
    # via the FE proxy, which has no session token in serverless). No secrets or
    # writes are exposed here, so auth is intentionally not required.
    # Mutating/operational endpoints (whitelist writes, dispatch, rss/new,
    # rss/health) remain behind require_monitor_auth.
    # P1 cache-share: if cron just wrote new chapters, bust local RSS cache (Redis shared flag)
    try:
        from modules.manhwa.tasks import _get_redis as _gr2
        if _gr2().get("rss:invalidate"):
            _RSS_CACHE.clear()
            _gr2().delete("rss:invalidate")
    except Exception as e:
        logger.warn("rss: redis cache invalidation failed", err=str(e)[:120])
    return await _rss_impl(request)

@router.get("/reader/rss")
async def rss_reader(request: Request):
    """Alias for FE compatibility — /api/v1/reader/rss → /api/v1/rss."""
    return await _rss_impl(request)

_ALLOWED_RSS_PARAMS = {
    "page", "limit", "group", "format", "type", "country", "origin",
    "source", "sources", "origins",
    "exclude", "exclude_origin",
    "q", "genres", "min_rating", "max_rating",
    "subscribed_only", "sort", "whitelist", "exclude_notified", "unread_only",
    "raw_only",
}

async def _rss_impl(request: Request):
    # P1 cache-share: if cron just wrote new chapters, bust local RSS cache (Redis shared flag)
    try:
        from modules.manhwa.tasks import _get_redis as _gr2
        if _gr2().get("rss:invalidate"):
            _RSS_CACHE.clear()
            _gr2().delete("rss:invalidate")
    except Exception as e:
        logger.warn("rss: redis cache invalidation failed", err=str(e)[:120])
    # Reject unknown query params (prevents cache-key variation / workload amplification)
    unknown = [k for k in request.query_params.keys() if k not in _ALLOWED_RSS_PARAMS]
    if unknown:
        return err(f"unknown query param: {unknown[0]}", status_code=400)
    cache_key = request.url.query
    cached = _rss_cache_get(cache_key)
    if cached is not None:
        try:
            import hashlib as _hl
            import json as _js
            etag = 'W/"' + _hl.sha256(_js.dumps(cached, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16] + '"'
            if request.headers.get("If-None-Match", "") == etag:
                return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, max-age=30, stale-while-revalidate=60", "Vary": "Cookie"})
            return JSONResponse(content=cached, headers={"ETag": etag, "Cache-Control": "private, max-age=30, stale-while-revalidate=60", "Vary": "Cookie"})
        except Exception:
            return JSONResponse(content=cached, headers={"Cache-Control": "private, max-age=30, stale-while-revalidate=60", "Vary": "Cookie"})

    page = int_safe(request.query_params.get("page", "1"), 1)
    if page < 1 or page > 100:
        return err("page must be between 1 and 100", status_code=400)
    try:
        limit = int_safe(request.query_params.get("limit", "50"), 50, max_val=100)
    except (ValueError, TypeError):
        limit = 50
    if limit > 100 or limit < 1:
        return err("limit must be between 1 and 100", status_code=400)
    group = (request.query_params.get("group", "true") or "true").lower() != "false"
    source_f = request.query_params.get("source", "")
    type_f = request.query_params.get("format", "") or request.query_params.get("type", "")
    origin_f = request.query_params.get("country", "") or request.query_params.get("origin", "")
    exclude = request.query_params.get("exclude", "")
    q = (request.query_params.get("q", "") or "")[:100]
    if len(request.query_params.get("q", "") or "") > 100:
        return err("q too long (max 100)", status_code=400)
    exclude_origin = request.query_params.get("exclude_origin", "")
    type_f = request.query_params.get("type", "")
    # Custom filters (merged from /rss/custom) — handled in Python post-filter for now
    genres_f = request.query_params.get("genres", "")
    min_rating = request.query_params.get("min_rating", "")
    max_rating = request.query_params.get("max_rating", "")
    subscribed_only = request.query_params.get("subscribed_only", "false").lower() == "true"
    sort_f = request.query_params.get("sort", "newest")
    # Reader feed shows ALL recent chapters by default (respects FE "all" feed).
    # Previously defaulted to True, which overrode the FE's "all" request and
    # hid every non-whitelisted title — making /recent look permanently stale.
    whitelist_only = request.query_params.get("whitelist", "false").lower() == "true" or subscribed_only
    # Default: show ALL chapters (including already-notified).
    # RSS is the discovery feed — hiding notified chapters breaks the
    # "see latest → add to whitelist" workflow. The FE marks isSent
    # on each chapter so users can see what's already been dispatched.
    # Use ?exclude_notified=true to hide them (for the "new only" view).
    _excl_raw = request.query_params.get("exclude_notified")
    if _excl_raw is None:
        exclude_notified = False
    else:
        exclude_notified = _excl_raw.lower() == "true"

    try:
        from modules.manhwa.services.rss_service import fetch_rss_data
        hours = 24  # RSS shows last 24h by design — fresh discovery feed
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        # (shinigami 121 in 24h, 1000 covers all + margin for filtering)
        if group:
            _fetch_limit = 2000
        else:
            _fetch_limit = 1000
        results, wl_map, sm_map, dh_sent = await fetch_rss_data(
            cutoff=cutoff,
            source_f=source_f,
            origin_f=origin_f,
            exclude=exclude,
            q=q,
            exclude_origin=exclude_origin,
            type_f=type_f,
            genres_f=genres_f,
            min_rating=min_rating,
            max_rating=max_rating,
            subscribed_only=subscribed_only,
            sort_f=sort_f,
            limit=limit,
            page=page,
            whitelist_only=whitelist_only,
            exclude_notified=exclude_notified,
            fetch_limit=_fetch_limit,
        )
        # raw_only is the inverse of whitelist_only: chapters whose
        # (title_key, source) pair is not subscribed. Applied before dedupe,
        # grouping, totals and pagination so counts stay honest.
        raw_only = request.query_params.get("raw_only", "false").lower() in ("1", "true")
        if raw_only:
            results = [r for r in results if not r.get("isWhitelisted")]
        # unread_only extra (not in service)
        unread_only = request.query_params.get("unread_only", "false").lower() == "true"
        if unread_only:
            results = [r for r in results if not r.get("isSent")]

        # genres/rating already filtered in fetch_rss_data — skip duplicate pass
        if sort_f == "rating":
            try:
                results.sort(key=lambda x: float(x.get("rating") or 0), reverse=True)
            except (ValueError, TypeError):
                pass
        elif sort_f == "popular":
            # popularity via rating as proxy (dispatch_count not in rss map)
            try:
                results.sort(key=lambda x: float(x.get("rating") or 0), reverse=True)
            except (ValueError, TypeError):
                pass
        # sources/origins plural (from /rss/custom)
        sources_f = request.query_params.get("sources", "")
        if sources_f:
            try:
                wanted_src = {s.strip().lower() for s in sources_f.split(",") if s.strip()}
                if wanted_src:
                    results = [r for r in results if str(r.get("source") or "").lower() in wanted_src]
            except (ValueError, TypeError):
                pass
        origins_f = request.query_params.get("origins", "")
        if origins_f:
            try:
                wanted_o = {o.strip().upper() for o in origins_f.split(",") if o.strip()}
                if wanted_o:
                    results = [r for r in results if str(r.get("origin") or "").upper() in wanted_o]
            except (ValueError, TypeError):
                pass

        # Per-source split: dedupe by (canonicalTitleKey, chapterNumber, source) — keep per-source rows
        if not group:
            _seen: dict[tuple[str, float, str], dict] = {}
            _deduped: list[dict] = []
            for r in results:
                ctk = r.get("canonicalTitleKey") or r.get("titleKey") or r.get("title") or ""
                # normalisasi biar "Chronicles Of The Lazy Sovereign " vs "Chronicles of the Lazy Sovereign" gak beda
                try:
                    from modules.manhwa.core.text import normalize_title_key as _ntk2
                    ctk = _ntk2(ctk)
                except Exception:
                    ctk = str(ctk).lower()
                cn = r.get("chapterNumber")
                if cn is None:
                    _deduped.append(r)
                    continue
                key = (ctk, float(cn), r.get("source") or "")
                if key in _seen:
                    # merge source into existing row's sources[]
                    _existing = _seen[key]
                    for s in (r.get("sources") or []):
                        if s not in _existing.get("sources", []):
                            _existing.setdefault("sources", []).append(s)
                    if not _existing.get("source"):
                        _existing["source"] = r.get("source")
                else:
                    _seen[key] = r
                    _deduped.append(r)
            results = _deduped

        if group:
            final_results = group_results(results)
        else:
            final_results = results

        total = len(final_results)
        total_pages = (total + limit - 1) // limit if limit else 1
        start = (page - 1) * limit
        paged = final_results[start:start + limit]
        # hasMore heuristic: if we fetched max and filtered still fills page, DB likely has more beyond fetch
        _has_more = page * limit < total
        if not _has_more and len(results) >= _fetch_limit and len(final_results) >= limit:
            _has_more = True
            # total underestimate when fetch truncated — bump for UI hasMore
            total = max(total, page * limit + 1)
            total_pages = (total + limit - 1) // limit if limit else 1

        body = {
            "success": True,
            "data": {
                "results": paged,
                "total": total,
                "page": page,
                "pageSize": limit,
                "limit": limit,
                "totalPages": total_pages,
                "hasMore": _has_more,
            },
        }
        _rss_cache_put(cache_key, body)
        # publik/non-sensitive → private max-age 30 SWR 60 (credentials:include but not shared cache)
        # whitelist-filtered variant tetap private (tidak bocor antar user via shared cache)
        etag = None
        try:
            import hashlib as _hl2
            import json as _js2
            etag = 'W/"' + _hl2.sha256(_js2.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16] + '"'
        except (TypeError, ValueError):
            pass
        headers = {"Cache-Control": "private, max-age=30, stale-while-revalidate=60", "Vary": "Cookie"}
        if etag:
            headers["ETag"] = etag
        return JSONResponse(content=body, headers=headers)

    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)

@router.get("/rss/new")
async def rss_new(request: Request):
    """Lightweight new-items counter."""
    if not require_monitor_auth(request):
        return unauthorized()
    raw = (request.query_params.get("since", "") or "").strip()
    if not raw:
        return err("missing since", status_code=400)
    since = raw
    try:
        val = float(raw)
        if val > 1e12:
            val = val / 1000.0
        since = datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        pass

    distinct = request.query_params.get("distinct", "all")
    raw_only = request.query_params.get("raw_only", "0") == "1"
    cache_key = f"{since}_{distinct}_{raw_only}"
    _now = _time.monotonic()
    if cache_key in _rss_new_cache:
        _cache_ts, _cache_val = _rss_new_cache[cache_key]
        if _now - _cache_ts < 30.0:
            resp = JSONResponse(content=_cache_val, headers={"Cache-Control": "private, no-store, must-revalidate", "Vary": "Cookie"})
            resp.headers["X-Cache"] = "HIT"
            return resp

    try:
        from modules.manhwa.db import q
        raw_clause = ""
        if raw_only:
            raw_clause = (
                " AND NOT EXISTS (SELECT 1 FROM whitelist w WHERE "
                "LOWER(w.source) = LOWER(recent_chapters.source) AND ("
                "w.title_key = recent_chapters.title_key OR "
                "replace(normalize_title_key(w.title_key), ' ', '-') = "
                "replace(normalize_title_key(recent_chapters.title_key), ' ', '-')))"
            )
        if distinct == "title":
            sql = f"SELECT COUNT(DISTINCT title_key) as cnt, MAX(updated_time) as latest FROM recent_chapters WHERE updated_time >= %s{raw_clause}"
        else:
            sql = f"SELECT COUNT(*) as cnt, MAX(updated_time) as latest FROM recent_chapters WHERE updated_time >= %s{raw_clause}"
        result = q(sql, [since])
        count = result[0]["cnt"] if result else 0
        latest = result[0]["latest"] if result else since
        payload = {"success": True, "data": {"since": since, "newCount": count, "latestUpdatedTime": latest}}
        _rss_new_cache[cache_key] = (_now, payload)
        if len(_rss_new_cache) > 500:
            _cutoff = _now - 60.0
            for k in [k for k, v in _rss_new_cache.items() if v[0] < _cutoff]:
                _rss_new_cache.pop(k, None)
        resp = JSONResponse(content=payload, headers={"Cache-Control": "private, no-store, must-revalidate", "Vary": "Cookie"})
        resp.headers["X-Cache"] = "MISS"
        return resp
    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)

@router.get("/rss/health")
async def rss_health(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    from modules.manhwa.storage import health as health_store
    from modules.manhwa.config import settings as _settings
    sources = health_store.load_source_health_map(_settings.SOURCE_KEYS) or {}
    for _sk in _settings.SOURCE_KEYS:
        if _sk not in sources:
            sources[_sk] = {"source": _sk, "status": "unknown", "consecutive_failures": 0}
    return ok({"sources": sources})
