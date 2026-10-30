"""Whitelist read path: pagination, metadata joins and response mapping.

Extracted from app/services/whitelist_service.py. whitelist_service re-exports
these symbols for backward compatibility with existing call sites and tests.
"""

from __future__ import annotations

import html

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger
from modules.manhwa.core.cover import scrub_cover
from modules.manhwa.core.text import escape_like
from modules.manhwa.services.whitelist.enrich import _fetch_whitelist_enrichment  # noqa: F401

logger = get_logger("services:whitelist")


def _fetch_whitelist_rows(
    source: str = "",
    title: str = "",
    page: int = 1,
    page_size: int = 100,
    merge: bool = True,
    cursor: str | None = None,
):
    from modules.manhwa.db import get_supabase
    from modules.manhwa.storage import whitelist as wl_store

    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(10000, int(page_size)))
    except (TypeError, ValueError):
        page_size = 100
    use_db_pagination = bool(source or title)
    if use_db_pagination:
        try:
            sb_pg = get_supabase()
            q = sb_pg.table("whitelist").select(
                "title_key, source, series_url, latest_sent_chapter, title, created_at, id, url, latest_chapter",
                count="exact",
            )
            if source:
                q = q.eq("source", source)
            if title:
                _t = escape_like(title)
                q = q.ilike("title", f"%{_t}%")
            cnt_res = q.limit(1).execute()
            total_raw = cnt_res.count or 0
            if merge:
                q2 = sb_pg.table("whitelist").select(
                    "title_key, source, series_url, latest_sent_chapter, title, created_at, id, url, latest_chapter"
                )
                if source:
                    q2 = q2.eq("source", source)
                if title:
                    _t2 = escape_like(title)
                    q2 = q2.ilike("title", f"%{_t2}%")
                q2 = q2.order("created_at", desc=True).limit(10000)
                rows = q2.execute().data or []
                return rows, total_raw, sb_pg, False
            else:
                if cursor:
                    q2 = sb_pg.table("whitelist").select(
                        "title_key, source, series_url, latest_sent_chapter, title, created_at, id, url, latest_chapter"
                    )
                    if source:
                        q2 = q2.eq("source", source)
                    if title:
                        _t2 = escape_like(title)
                        q2 = q2.ilike("title", f"%{_t2}%")
                    q2 = q2.lt("created_at", cursor).order("created_at", desc=True).limit(page_size)
                    rows = q2.execute().data or []
                    return rows, total_raw, sb_pg, True
                start = (page - 1) * page_size
                q2 = sb_pg.table("whitelist").select(
                    "title_key, source, series_url, latest_sent_chapter, title, created_at, id, url, latest_chapter"
                )
                if source:
                    q2 = q2.eq("source", source)
                if title:
                    _t2 = escape_like(title)
                    q2 = q2.ilike("title", f"%{_t2}%")
                q2 = q2.order("created_at", desc=True).limit(page_size).offset(start)
                rows = q2.execute().data or []
                return rows, total_raw, sb_pg, True
        except Exception as e:
            logger.warn("whitelist_repo DB pagination failed, fallback in-memory", err=str(e)[:160])
    rows = wl_store.load_whitelist()
    if source:
        rows = [r for r in rows if (r.get("source") or "") == source]
    if title:
        _ql = title.lower()
        rows = [r for r in rows if _ql in (r.get("title", "") or "").lower()]
    from modules.manhwa.db import get_supabase

    sb = get_supabase()
    return rows, len(rows), sb, False


def get_whitelist(
    source: str = "",
    title: str = "",
    page: int = 1,
    page_size: int = 100,
    merge: bool = False,
    cursor: str | None = None,
) -> dict:
    """Get whitelist with metadata joins. DB-side pagination + cursor (keyset)."""

    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(10000, int(page_size)))
    except (TypeError, ValueError):
        page_size = 100

    rows, total, sb, _db_paginated_flag = _fetch_whitelist_rows(
        source=source, title=title, page=page, page_size=page_size, merge=merge, cursor=cursor
    )
    all_tks = [r.get("title_key") for r in rows if r.get("title_key")]
    offset = (page - 1) * page_size
    total_pages = (total + page_size - 1) // page_size if page_size else 1
    has_more = page * page_size < total

    (
        rc_map,
        meta_desc,
        meta_cover,
        meta_rating,
        meta_genres,
        meta_type,
        meta_origin,
        last_notified,
    ) = _fetch_whitelist_enrichment(sb, rows, all_tks)
    mapped = [
        build_whitelist_mapped_row(
            r,
            rc_map,
            meta_desc,
            meta_cover,
            meta_rating,
            meta_genres,
            meta_type,
            meta_origin,
            last_notified,
        )
        for r in rows
    ]

    _is_db_paginated = bool(_db_paginated_flag)
    if merge:
        deduped = dedup_whitelist(mapped)
    else:
        deduped = mapped

    if _is_db_paginated:
        total = total  # already count from repo
        total_pages = (total + page_size - 1) // page_size if page_size else 1
        has_more = page * page_size < total
        paged_out = deduped
        # Sort BEFORE slice already done in repo; keep order (fix sort-after-slice bug was here)
    else:
        total = len(deduped)
        total_pages = (total + page_size - 1) // page_size if page_size else 1
        has_more = page * page_size < total
        offset = (page - 1) * page_size
        deduped.sort(
            key=lambda x: (
                x.get("last_chapter") or x.get("last_notified") or "",
                x.get("title") or "",
            ),
            reverse=True,
        )
        paged_out = deduped[offset : offset + page_size]
    next_cursor = paged_out[-1].get("created_at") if has_more and paged_out else None
    return {
        "success": True,
        "data": {
            "results": paged_out,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "limit": page_size,
            "totalPages": total_pages,
            "hasMore": has_more,
            "nextCursor": next_cursor,
        },
    }


def build_whitelist_mapped_row(
    r: dict,
    rc_map: dict,
    meta_desc: dict,
    meta_cover: dict,
    meta_rating: dict,
    meta_genres: dict,
    meta_type: dict,
    meta_origin: dict,
    last_notified: dict,
) -> dict:
    """Build a single whitelist response row with metadata joins.

    Shared by get_whitelist() to map storage rows to API response format.
    rc_map is keyed by (title_key, source) so each whitelist row
    vs shinigami for the same title) gets its OWN recent_chapters
    series_url instead of an arbitrary cross-source one.
    """
    tk = r.get("title_key", "")
    s = r.get("source", "")
    rc = rc_map.get((tk, s)) or rc_map.get(tk, {})
    _wl_raw = r.get("series_url") or ""
    _wl_series = (
        _wl_raw
        if str(_wl_raw).startswith(("http://", "https://"))
        else (rc.get("series_url") or "")
    )
    if not _wl_series:
        _u = str(r.get("url") or "").strip()
        if _u.startswith(("http://", "https://")):
            _wl_series = _u
        elif tk and s == "shinigami":
            if rc.get("series_url"):
                _wl_series = rc.get("series_url") or ""
            elif len(tk) == 36 and tk.count("-") == 4:
                # shinigami UUID directly
                _wl_series = f"{settings.SHINIGAMI_PUBLIC_BASE}/series/{tk}"
            elif " " not in tk:
                _wl_series = f"{settings.SHINIGAMI_PUBLIC_BASE}/series/{tk}"
    # cover must be a real http(s) URL; scrapers/FE sometimes store 'x' or
    # other non-URL placeholders — fall through to recent_chapters cover.
    _wl_cover = r.get("cover")
    _cover = (
        _wl_cover
        if str(_wl_cover or "").startswith(("http://", "https://"))
        else (rc.get("cover") or "")
    )
    if _cover:
        _cover = scrub_cover(_cover)
    desc = r.get("description") or meta_desc.get(tk) or meta_desc.get(tk.replace(" ", "-")) or None
    if desc:
        desc = desc.replace("\ufffd", "\u2019").replace("\u0092", "\u2019")
    _meta_cov = meta_cover.get(tk) or meta_cover.get(" ".join(tk.split("-"))) or ""
    if _meta_cov:
        _meta_cov = scrub_cover(_meta_cov)
    _rc_rating = rc.get("rating")
    _rc_genres = rc.get("genres")
    _rc_type = rc.get("type")
    _rc_origin = rc.get("origin")
    # meta fallback for rating/genres via series_meta (already in rc, but keep for old rows)
    _meta_rating = (
        meta_cover.get(f"{tk}:rating") or ""
    )  # placeholder, actual meta_rating via _fetch
    return {
        "id": str(r.get("id") or f"{tk}:{s}" if s else tk),
        "title": html.unescape(r.get("title") or "")
        .replace("\ufffd", "\u2019")
        .replace("\u0092", "\u2019"),
        "titleKey": tk,
        "cover": _cover or _meta_cov or "",
        "source": s,
        "rating": r.get("rating") or _rc_rating or meta_rating.get(tk) or None,
        "format": r.get("type") or _rc_type or meta_type.get(tk) or None,
        "country": r.get("origin") or _rc_origin or meta_origin.get(tk) or "",
        "genres": r.get("genres") or _rc_genres or meta_genres.get(tk) or [],
        "description": desc or rc.get("description") or "",
        "seriesUrl": _wl_series,
        "createdAt": r.get("created_at") or None,
    }




def dedup_whitelist(mapped: list[dict]) -> list[dict]:
    if not mapped:
        return []
    groups: dict[str, list[dict]] = {}
    for m in mapped:
        nk = m["titleKey"]
        groups.setdefault(nk, []).append(m)
    deduped: list[dict] = []
    for _items in groups.values():
        if len(_items) == 1:
            deduped.append(_items[0])
            continue
        _primary = _items[0]
        _sources: list[dict] = []
        _seen_src: set[str] = set()
        for _it in _items:
            for _s in _it.get("sources", []):
                if _s not in _seen_src:
                    _seen_src.add(_s)
                    _sources.append({"source": _s, "url": _it.get("series_url") or ""})
        _merged = dict(_primary)
        _merged["sources"] = [s["source"] for s in _sources]
        _merged["source"] = _sources[0]["source"] if _sources else (_primary.get("source") or "")
        _merged["source_detail"] = _sources
        for _fld in ("cover", "origin", "rating", "description", "type"):
            for _it in _items:
                if _it.get(_fld):
                    _merged[_fld] = _it[_fld]
                    break
        _genres: list[str] = []
        for _it in _items:
            for _g in _it.get("genres") or []:
                if _g not in _genres:
                    _genres.append(_g)
        _merged["genres"] = _genres
        _merged["created_at"] = max(
            [_it.get("created_at") or "" for _it in _items]
        ) or _primary.get("created_at")
        for _fld in ("latest_chapter", "latest_sent_chapter"):
            _vals = [_it.get(_fld) for _it in _items if _it.get(_fld) is not None]
            _merged[_fld] = max(_vals) if _vals else None
        deduped.append(_merged)
    return deduped
