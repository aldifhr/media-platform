"""Dispatch history service — flat list of dispatched (notified) chapters.

Extracted from whitelist_service.py to separate the dispatch-history concern
from whitelist CRUD.
"""

from __future__ import annotations

import html
import re
import time

from modules.manhwa.core.cover import scrub_cover
from modules.manhwa.core.origin import normalize_origin
from modules.manhwa.core.text import normalize_shinigami_url, normalize_title_key
from modules.manhwa.logger import get_logger
from modules.manhwa.services.rss_query import chapter_label

logger = get_logger("services:dispatch_history")

# Cache for dispatch-history GET keyed by (page, page_size, search).
_DH_CACHE: list = [0.0, None, None]  # [ts, key, payload]
_DH_TTL = 15.0


def get_dispatch_history(page: int = 1, page_size: int = 50, search: str = "") -> dict:
    """Flat list of all dispatched (notified) chapters from dispatch_history,
    joined with recent_chapters for cover/origin/status. One row per chapter."""
    from modules.manhwa.db import get_supabase

    _dh_key = (page, page_size, search)
    _now = time.monotonic()
    if _DH_CACHE[0] is not None and _DH_CACHE[1] == _dh_key and (_now - _DH_CACHE[0]) < _DH_TTL:
        return _DH_CACHE[2]

    sb = get_supabase()
    offset = (page - 1) * page_size

    # H6 Fix: Push search filter to DB with ILIKE
    if search:
        search_pattern = f"%{search}%"
        try:
            cnt_res = (
                sb.table("dispatch_history")
                .select("chapter_url", count="exact")
                .ilike("title_key", search_pattern)
                .execute()
            )
            total_for_search = cnt_res.count or 0
        except Exception:
            total_for_search = 0
        dh = (
            sb.table("dispatch_history")
            .select(
                "chapter_url, title_key, source, chapter_title, "
                "sent_at, cover, series_url, fcfs_key"
            )
            .ilike("title_key", search_pattern)
            .order("sent_at", desc=True)
            .limit(page_size)
            .offset(offset)
            .execute()
        )
        rows = dh.data or []
    else:
        try:
            cnt_res = (
                sb.table("dispatch_history").select("chapter_url", count="exact").limit(1).execute()
            )
            total_for_search = cnt_res.count or 0
        except Exception:
            total_for_search = 0
        dh = (
            sb.table("dispatch_history")
            .select(
                "chapter_url, title_key, source, chapter_title, "
                "sent_at, cover, series_url, fcfs_key"
            )
            .order("sent_at", desc=True)
            .limit(page_size)
            .offset(offset)
            .execute()
        )
        rows = dh.data or []

    _dup_keys: dict[str, str] = {}
    _dup_set: set[int] = set()

    def invalidate_dh_cache():
        """Reset the dispatch-history cache after whitelist mutation."""
        global _DH_CACHE
        _DH_CACHE = [0.0, None, None]

    raw_urls = [r["chapter_url"] for r in rows if r.get("chapter_url")]
    urls = [normalize_shinigami_url(u) or u for u in raw_urls]
    rc_map = {}
    wl_map = {}
    # Store title per title_key from recent_chapters as fallback
    rc_title_map: dict[str, str] = {}
    # BUG4: recent_chapters is pruned to 24h but dispatch_history retains 90d,
    # so chapter_url joins often miss. Build a series-level metadata map from
    # recent_chapters by (title_key, source) — that table carries genres/
    # description/rating/origin/cover per series.
    _tk_src: set[tuple[str, str]] = set()
    for r in rows:
        tk = r.get("title_key") or ""
        src = r.get("source") or ""
        if tk and src:
            _tk_src.add((tk, src))
    if _tk_src:
        try:
            _ph = ",".join(["%s"] * len(_tk_src))
            _cur = (
                get_supabase()
                .table("recent_chapters")
                .select(
                    "title_key, source, title, chapter, cover, origin, "
                    "genres, description, rating, series_url"
                )
            )
            # supabase in_ with tuple list isn't supported; loop small set
            for _tk, _src in _tk_src:
                _rc = (
                    get_supabase()
                    .table("recent_chapters")
                    .select(
                        "title_key, source, title, chapter, cover, origin, "
                        "genres, description, rating, series_url"
                    )
                    .eq("title_key", _tk)
                    .eq("source", _src)
                    .limit(1)
                    .execute()
                )
                for _row in _rc.data or []:
                    rc_map[(_tk, _src)] = _row
                    if _row.get("title"):
                        rc_title_map[_tk] = str(_row["title"])
        except Exception as e:
            logger.warn("dispatch_history: rc lookup failed", err=str(e)[:120])
    if urls:
        rc = (
            sb.table("recent_chapters")
            .select("chapter_url, title, chapter, cover, origin, series_url")
            .in_("chapter_url", urls)
            .execute()
        )
        for rc_row in rc.data or []:
            rc_map[rc_row["chapter_url"]] = rc_row
    wl = (
        sb.table("whitelist")
        .select("title, title_key")
        .in_("title_key", [r.get("title_key") or "" for r in rows if r.get("title_key")])
        .execute()
    )
    for wl_row in wl.data or []:
        wl_map[wl_row.get("title_key")] = wl_row
        wl_map[f"title:{normalize_title_key(wl_row.get('title_key') or '')}"] = wl_row
    # BUG4 fallback: series_meta carries static metadata per (title_key, source)
    sm_map: dict[tuple[str, str], dict] = {}
    # `title` is not a column on series_meta — the schema is
    # title_key, source, rating, genres, description, cover, author, type,
    # updated_at, origin. Selecting it raised UndefinedColumn, which the
    # except below swallowed into a warn, so sm_map stayed empty and every
    # dispatch_history row lost its metadata fallback (cover/rating/description)
    # for as long as the bug was live.
    try:
        _sm = (
            sb.table("series_meta")
            .select("title_key, source, genres, description, rating, cover, type")
            .in_("title_key", [r.get("title_key") or "" for r in rows if r.get("title_key")])
            .execute()
        )
        for _s in _sm.data or []:
            sm_map[(_s.get("title_key"), _s.get("source"))] = _s
    except Exception as e:
        logger.warn("dispatch_history: series_meta lookup failed", err=str(e)[:120])

    results = []
    for idx, r in enumerate(rows):
        _raw = r.get("chapter_url") or ""
        _norm = normalize_shinigami_url(_raw) or _raw
        _dup_key = ""
        _ch_raw = r.get("chapter_title") or ""
        _m = re.search(r"(\d+(?:\.\d+)?)", str(_ch_raw))
        if _m:
            # BUG3: dedupe by (normalized title_key, float chapter) across sources.
            # A chapter dispatched twice (same or different source) is a duplicate.
            _tk_norm = normalize_title_key(r.get("title_key", "") or "")
            try:
                _ch_num = float(_m.group(1))
            except ValueError:
                _ch_num = None
            if _ch_num is not None:
                _dup_key = f"{_tk_norm}|{_ch_num}"
                if _dup_key in _dup_keys:
                    _dup_set.add(idx)
                else:
                    _dup_keys[_dup_key] = r.get("sent_at") or ""
        rc = rc_map.get((r.get("title_key"), r.get("source"))) or rc_map.get(_norm, {})
        wl = wl_map.get(r.get("title_key", ""), {})
        if not wl:
            tk_norm = normalize_title_key(r.get("chapter_title") or r.get("title_key") or "")
            wl = wl_map.get(f"title:{tk_norm}", {})

        # BUG4: metadata from recent_chapters (has genres/description/rating/origin)
        # falls back to whitelist, then series_meta, then dispatch row.
        _tk = r.get("title_key") or ""
        _src = r.get("source") or ""
        _sm = sm_map.get((_tk, _src)) or {}
        _genres = rc.get("genres") or wl.get("genres") or _sm.get("genres") or []
        _desc = rc.get("description") or wl.get("description") or _sm.get("description") or ""
        _rating = (
            rc.get("rating")
            if rc.get("rating") is not None
            else (
                wl.get("rating")
                if wl.get("rating") is not None
                else (_sm.get("rating") if _sm.get("rating") is not None else None)
            )
        )
        _origin = normalize_origin(rc.get("origin") or wl.get("origin") or _sm.get("origin") or "")
        title = (
            rc.get("title")
            or wl.get("title")
            or rc_title_map.get(_tk)
            or r.get("title_key")
            or r.get("chapter_title")
            or "Untitled"
        )
        title = html.unescape(title).replace("\ufffd", "\u2019").replace("\u0092", "\u2019")
        _desc = (_desc or "").replace("\ufffd", "\u2019").replace("\u0092", "\u2019")
        # BUG5: scrub cover (presigned -> proxy-in)
        _cover = scrub_cover(r.get("cover") or rc.get("cover") or "")
        results.append(
            {
                "title": title,
                "cover": _cover,
                "sentAt": r.get("sent_at") or "",
                "source": r.get("source") or rc.get("source") or "",
                "chapterLabel": chapter_label(
                    str(r.get("chapter_title") or rc.get("chapter") or "")
                ),
                "seriesUrl": r.get("series_url") or rc.get("series_url") or "",
            }
        )

    if search:
        results = [x for x in results if search in x["title"].lower()]
        total = len(results)
        paged_results = results[offset : offset + page_size]
    else:
        total = total_for_search
        paged_results = results
    total_pages = (total + page_size - 1) // page_size if page_size else 1
    payload = {
        "success": True,
        "data": {
            "results": paged_results,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "limit": page_size,
            "totalPages": total_pages,
            "hasMore": page * page_size < total,
            "has_more": page * page_size < total,
        },
    }
    _DH_CACHE[0] = time.monotonic()
    _DH_CACHE[1] = _dh_key
    _DH_CACHE[2] = payload
    return payload
