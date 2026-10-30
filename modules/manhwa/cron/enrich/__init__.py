"""Chapter enrichment + backfill (metadata attach, 24h split).

This module has been consolidated: heavy enrichment logic now lives in
app/services/scraper_service.py. The public symbols here remain exported
(via pipeline.py) for backward compatibility.
"""
from __future__ import annotations

import concurrent.futures
import html as _html
import re

from modules.manhwa.logger import get_logger
from modules.manhwa.sources import shinigami
from modules.manhwa.sources.shinigami import _country_to_type
from modules.manhwa.storage import metadata as meta_store
from modules.manhwa.core.origin import normalize_origin

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

logger = get_logger("cron:enrich")

# Distinct non-numeric user_rate values already logged, so a bad upstream
# value is reported once instead of once per item.
_bad_rate_logged: set[str] = set()


def _safe_rate(v) -> float:
    """user_rate can arrive as a non-numeric string from upstream.

    A bare float() raised ValueError and dropped the whole enrichment item
    (the exception escaped the worker future). A bad rating must cost one
    field, not the item.
    """
    if v in (None, "", 0):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        key = str(v)[:80]
        if key not in _bad_rate_logged:
            _bad_rate_logged.add(key)
            logger.warn("non-numeric user_rate, defaulting to 0.0", value=key)
        return 0.0

def _strip_html(s: str) -> str:
    """Remove HTML tags and collapse whitespace from a description."""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", str(s))
    s = _html.unescape(s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def enrich(items: list[dict], persist_cache: bool = False, skip_api: bool = False) -> list[dict]:
    """Attach metadata (cover/status/rating). Check whitelist cache first, fallback to API.

    skip_api=True → never hit the source APIs; use only the whitelist cache.
    This is used by the live /api/rss endpoint so a page load doesn't trigger
    100+ sequential API calls (which took ~11s). The cron rss-fetch pass already
    populated whitelist, so the cache is fresh enough for web display.
    """

    def _shinigami_id(it: dict) -> str | None:
        return it["series_url"].rstrip("/").split("/")[-1] if it.get("series_url") else None

    # ── Collect keys per source ──
    shin_mids: list[str] = []
    shin_idx: dict[str, list[int]] = {}
    for i, it in enumerate(items):
        if it.get("source") == "shinigami":
            mid = _shinigami_id(it)
            if mid:
                shin_mids.append(mid)
                shin_idx.setdefault(mid, []).append(i)

    # ── Cache load (by title_key) ──
    cache_keys = list(set(shin_mids))
    cached: dict[str, dict] = {}
    if cache_keys:
        rows = meta_store.batch_get_manga_metadata(cache_keys)
        for key, row in zip(cache_keys, rows):
            if row:
                if row.get("origin") and row["origin"] not in ("KR", "CN", "JP"):
                    row["origin"] = normalize_origin(row.get("origin"))
                cached[key] = row

    # ── Shinigami enrich (parallelized) ──
    def _fetch_shin(mid: str) -> tuple[str, dict | None]:
        # Fetch-mode shinigami rows already carry rating/cover/origin from the
        # latest-updates list (see _collect_shinigami_source). Don't burn the
        # strict per-IP rate limit re-fetching series we already have data for —
        # only hit the API when the row is missing rating AND cover.
        _idx = shin_idx.get(mid, [])
        _it = items[_idx[0]] if _idx else {}
        if _it.get("rating") not in (None, 0) and _it.get("cover"):
            return mid, None
        if mid in cached and cached[mid].get("origin"):
            return mid, None
        if skip_api:
            return mid, None
        try:
            s = shinigami.get_shinigami_series(mid)
        except Exception:
            s = None
        if not s:
            return mid, None
        status_map = {"1": "ongoing", "2": "completed", "3": "hiatus"}
        tax = s.get("taxonomy") or []
        if isinstance(tax, dict):
            genres = [g.get("name") for g in tax.get("Genre", []) if g.get("name")]
        elif isinstance(tax, list):
            genres = [t for t in tax if isinstance(t, str)]
        else:
            genres = []
        _shin_new = normalize_origin(s.get("country_id"))
        _shin_old = (cached.get(mid, {}) or {}).get("origin") or ""
        _shin_origin = _shin_new if _shin_new in ("KR", "CN", "JP") else _shin_old
        mapped = {
            "title_key": mid,
            "source": "shinigami",
            "cover": s.get("cover_image_url") or s.get("cover_portrait_url") or "",
            "status": status_map.get(str(s.get("status")), "unknown"),
            "rating": _safe_rate(s.get("user_rate")),
            "genres": genres,
            "description": s.get("description") or s.get("synopsis") or "",
            "origin": _shin_origin,
            "type": _country_to_type(s.get("country_id")) or s.get("type") or "",
        }
        return mid, mapped

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_shin, mid): mid for mid in set(shin_mids)}
        for fut in concurrent.futures.as_completed(futures):
            mid, mapped = fut.result()
            if not mapped:
                continue
            cached[mid] = mapped
            # Do NOT auto-upsert to whitelist on RSS fetch — that's enrich_whitelist's job
            # if persist_cache:
            #     meta_store.upsert_manga_metadata([mapped])

    # ── Shinigami apply to items ──
    for mid, indices in shin_idx.items():
        s = cached.get(mid)
        if not s:
            continue
        shin_origin = normalize_origin(s.get("origin"))
        for i in indices:
            it = items[i]
            # Preserve list-provided values; only fill gaps from series_meta.
            it["cover"] = it.get("cover") or s.get("cover") or ""
            it["status"] = s.get("status", "unknown") or it.get("status", "")
            if not it.get("rating"):
                it["rating"] = s.get("rating", "")
            if not it.get("genres"):
                it["genres"] = s.get("genres", [])
            it["description"] = _strip_html(s.get("description", "")) or it.get("description", "")
            # Don't overwrite a valid collector origin with API origin — shinigami
            # API country_id is unreliable (hosts CN content, reports KR). Collector
            # origin is the source of truth after the 31194e0 fix; only fill when empty.
            if not it.get("origin"):
                it["origin"] = shin_origin
            # Update type from cached shinigami data if available
            if not it.get("type") and s.get("type"):
                it["type"] = s["type"]

    # Final safety: ensure every item's origin is a canonical country code
    # and metadata keeps the types the Discord embed builder expects.
    #
    # The claim path can receive jsonb genres as a string representation and
    # missing ratings as the literal string "None". Passing those through made
    # the embed iterate genre characters (", A, c...") and render a valid rating
    # as `No rating`. Normalize once at the seam instead of adding source-specific
    # fixes in every collector.
    import ast as _ast
    import json as _json
    for it in items:
        if it.get("origin"):
            it["origin"] = normalize_origin(it["origin"])
        _genres = it.get("genres")
        if isinstance(_genres, str):
            _parsed = None
            try:
                _parsed = _json.loads(_genres)
            except (TypeError, ValueError):
                try:
                    _parsed = _ast.literal_eval(_genres)
                except (ValueError, SyntaxError):
                    _parsed = None
            if isinstance(_parsed, list):
                _genres = _parsed
            elif _genres.strip():
                _genres = [g.strip() for g in _genres.split(",") if g.strip()]
            else:
                _genres = []
        it["genres"] = [str(g).strip() for g in (_genres or []) if str(g).strip()]
        if str(it.get("rating") or "").strip().lower() in {"none", "null", "n/a"}:
            it["rating"] = ""

    return items

from modules.manhwa.services.dispatch.backfill import (  # noqa: E402,F401
    _split_send_backfill,
    backfill_dispatch_history,
    mark_history_only,
)
