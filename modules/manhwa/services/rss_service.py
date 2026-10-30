"""RSS service — extracted from app/api/rss.py for testability."""
from __future__ import annotations

import asyncio

from modules.manhwa.logger import get_logger
from modules.manhwa.core.text import escape_like, normalize_title_key
from modules.manhwa.services.rss_query import build_filter, map_result

logger = get_logger("services:rss_service")

def _fetch_rss_data_sync(
    *,
    cutoff: str,
    source_f: str = "",
    origin_f: str = "",
    exclude: str = "",
    q: str = "",
    exclude_origin: str = "",
    type_f: str = "",
    genres_f: str = "",
    min_rating: str = "",
    max_rating: str = "",
    subscribed_only: bool = False,
    sort_f: str = "newest",
    limit: int = 500,
    page: int = 1,
    whitelist_only: bool = False,
    exclude_notified: bool = False,
    fetch_limit: int = 1000,
):
    """Sync core — all psycopg2 calls block; run via to_thread from async wrapper."""
    from modules.manhwa.db import get_supabase

    sb = get_supabase()

    rc_q = (
        sb.table("recent_chapters")
        .select(
            "chapter_url, title_key, title, chapter, chapter_num, source, cover, origin, updated_time, release_date, created_at, series_url, description, type, rating, genres"
        )
        .gte("updated_time", cutoff)
    )
    if source_f:
        rc_q = rc_q.eq("source", source_f)
    if origin_f:
        rc_q = rc_q.eq("origin", origin_f.upper())
    # exclude_origin is applied in Python after fetch so NULL origins are kept.
    _excl_origin = set()
    if exclude_origin:
        _excl_origin = {e.strip().upper() for e in exclude_origin.split(",") if e.strip()}
    if type_f:
        rc_q = rc_q.eq("type", type_f.lower())
    if q:
        rc_q = rc_q.ilike("title", f"%{escape_like(q)}%")
    rc_rows = rc_q.order("updated_time", desc=True).limit(fetch_limit).execute().data or []
    if _excl_origin:
        rc_rows = [r for r in rc_rows if r.get("origin") is None or r.get("origin", "").upper() not in _excl_origin]

    if exclude_notified:
        try:
            from modules.manhwa.db import q as _raw_q
            _where = ["rc.updated_time >= %s"]
            _params: list = [cutoff]
            if source_f:
                _where.append("rc.source = %s")
                _params.append(source_f)
            if origin_f:
                _where.append("rc.origin = %s")
                _params.append(origin_f.upper())
            if exclude_origin:
                for _o in [e.strip().upper() for e in exclude_origin.split(",") if e.strip()]:
                    _where.append("(rc.origin != %s OR rc.origin IS NULL)")
                    _params.append(_o)
            if type_f:
                _where.append("rc.type = %s")
                _params.append(type_f.lower())
            if q:
                _where.append("rc.title ILIKE %s")
                _params.append(f"%{escape_like(q)}%")
            _where.append(
                "NOT EXISTS (SELECT 1 FROM dispatch_history dh "
                "WHERE dh.title_key = rc.title_key AND dh.source = rc.source "
                "AND dh.chapter_title ~ '^[0-9]+(\\.[0-9]+)?$' "
                "AND CASE WHEN dh.chapter_title ~ '^[0-9]+(\\.[0-9]+)?$' THEN dh.chapter_title::float END = rc.chapter_num AND dh.sent_at >= %s)"
            )
            _params.append(cutoff)
            _sql = (
                "SELECT chapter_url, title_key, title, chapter, chapter_num, source, "
                "cover, origin, updated_time, release_date, created_at, series_url, description, type "
                f"FROM recent_chapters rc WHERE {' AND '.join(_where)} "
                "ORDER BY rc.updated_time DESC LIMIT %s"
            )
            _params.append(fetch_limit)
            rc_rows = _raw_q(_sql, _params) or []
        except Exception as _e:
            logger.warn("exclude_notified SQL failed, falling back to unfiltered", err=str(_e)[:160])

    _rc_tks = list({str(r.get("title_key") or "") for r in rc_rows if r.get("title_key")})
    wl_rows = []
    try:
        if _rc_tks:
            for i in range(0, len(_rc_tks), 100):
                chunk = _rc_tks[i:i+100]
                wl_rows.extend(sb.table("whitelist").select(
                    "title_key, source, series_url, latest_sent_chapter"
                ).in_("title_key", chunk).execute().data or [])
    except Exception:
        wl_rows = wl_rows or []
    sm_rows = []
    try:
        if _rc_tks:
            for i in range(0, len(_rc_tks), 100):
                chunk = _rc_tks[i:i+100]
                sm_rows.extend(sb.table("series_meta").select(
                    "title_key, source, rating, genres, description, cover, type"
                ).in_("title_key", chunk).execute().data or [])
    except Exception:
        sm_rows = sm_rows or []

    wl_map: dict[tuple[str, str], dict] = {}
    wl_title_set: set[str] = set()
    for w in wl_rows:
        tk = str(w.get("title_key", "") or "")
        src = str(w.get("source", "") or "")
        if tk:
            nk = normalize_title_key(tk)
            wl_map[(nk, src)] = w
            wl_map[(tk, src)] = w
        t_norm = normalize_title_key(w.get("title") or tk)
        if t_norm:
            wl_title_set.add(t_norm)

    sm_map: dict[tuple[str, str], dict] = {}
    for s in sm_rows:
        stk = str(s.get("title_key") or "")
        ssrc = str(s.get("source") or "")
        if stk and ssrc:
            sm_map[(stk, ssrc)] = s

    dh_sent: set[tuple[str, float]] = set()
    try:
        # scope dh to rc titles + cutoff (was full 2000 scan then Python filter)
        if _rc_tks:
            dh_rows = []
            for i in range(0, len(_rc_tks), 100):
                chunk = _rc_tks[i:i+100]
                dh_rows.extend(sb.table("dispatch_history").select("title_key, source, chapter_title").gte("sent_at", cutoff).in_("title_key", chunk).limit(500).execute().data or [])
        else:
            dh_rows = sb.table("dispatch_history").select("title_key, source, chapter_title").gte("sent_at", cutoff).limit(500).execute().data or []
        for dh in dh_rows:
            tk = str(dh.get("title_key") or "")
            ct = dh.get("chapter_title")
            try:
                cn = float(ct) if ct is not None else None
            except (ValueError, TypeError):
                cn = None
            if tk and cn is not None:
                dh_sent.add((tk, cn))
                nk = normalize_title_key(tk)
                if nk != tk:
                    dh_sent.add((nk, cn))
    except Exception as e:
        logger.warn("rss_service: dispatch_history load failed", err=str(e)[:120])

    _passes = build_filter(source_f, origin_f, exclude, q, exclude_origin, type_f)
    filtered = [it for it in rc_rows if _passes(it)]

    results = [map_result(it, wl_map, sm_map, dh_sent, wl_title_set) for it in filtered]

    if whitelist_only or subscribed_only:
        results = [r for r in results if r["isWhitelisted"]]

    if genres_f:
        try:
            wanted = {g.strip().lower() for g in genres_f.split(",") if g.strip()}
            if wanted:
                results = [r for r in results if wanted & {str(g).lower() for g in (r.get("genres") or [])}]
        except (ValueError, TypeError):
            pass
    if min_rating:
        try:
            mv = float(min_rating)
            results = [r for r in results if r.get("rating") is not None and float(r.get("rating") or 0) >= mv]
        except (ValueError, TypeError):
            pass
    if max_rating:
        try:
            mv = float(max_rating)
            results = [r for r in results if r.get("rating") is not None and float(r.get("rating") or 0) <= mv]
        except (ValueError, TypeError):
            pass
    return results, wl_map, sm_map, dh_sent

async def fetch_rss_data(*args, **kwargs):
    """Async wrapper — offloads sync psycopg2 to thread pool so event loop not blocked.

    Keeps same signature as sync core for drop-in await compatibility (rss.py await fetch_rss_data).
    """
    return await asyncio.to_thread(lambda: _fetch_rss_data_sync(*args, **kwargs))
