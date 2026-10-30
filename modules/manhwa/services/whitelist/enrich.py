"""Whitelist enrichment lookups — rc/meta/dh maps for get_whitelist.

Split out of query.py. Isolated for testability.
"""
from __future__ import annotations

from modules.manhwa.core.text import slugify_title_key
from modules.manhwa.logger import get_logger

logger = get_logger("services:whitelist")


def _fetch_whitelist_enrichment(sb, rows: list[dict], all_tks: list[str]):
    """Fetch rc/meta/dh maps for get_whitelist — isolated for testability."""
    rc_map: dict = {}
    meta_desc: dict[str, str] = {}
    meta_cover: dict[str, str] = {}
    meta_rating: dict[str, float] = {}
    meta_genres: dict[str, list] = {}
    meta_type: dict[str, str] = {}
    meta_origin: dict[str, str] = {}
    last_notified: dict[str, str] = {}

    # series_meta is keyed by the slug title_key only (migration 080 normalized
    # away the legacy UUID keys). Do NOT add the series_url last segment here —
    # for shinigami that segment is a UUID and would probe a key that no longer
    # exists.
    cand_keys: set[str] = set()
    for r in rows:
        tk = r.get("title_key", "")
        if tk:
            cand_keys.add(tk)
            cand_keys.add(slugify_title_key(tk))

    def _q_recent():
        q = sb.table("recent_chapters").select(
            "title_key, title, source, cover, origin, updated_time, series_url, rating, genres, description, type"
        )
        if all_tks:
            q = q.in_("title_key", all_tks)
        return q.execute()

    def _q_meta():
        if not cand_keys:
            return None
        return (
            sb.table("series_meta")
            .select("title_key, description, cover, rating, genres, type, origin")
            .in_("title_key", list(cand_keys))
            .execute()
        )

    def _q_dh():
        tks = [r.get("title_key", "") for r in rows if r.get("title_key")]
        if not tks:
            return None
        return (
            sb.table("dispatch_history")
            .select("title_key, sent_at")
            .in_("title_key", tks)
            .execute()
        )

    rc_rows = _q_recent()
    meta_rows = _q_meta()
    dh_rows = _q_dh()

    if rc_rows and rc_rows.data:
        for rc in rc_rows.data:
            tk = rc.get("title_key")
            src = rc.get("source") or ""
            if tk and (tk, src) not in rc_map:
                rc_map[(tk, src)] = rc
    try:
        _meta_data = meta_rows.data if (meta_rows and getattr(meta_rows, "data", None)) else []
        for m in _meta_data:
            d = m.get("description")
            if d:
                meta_desc[m.get("title_key", "")] = d
            c = m.get("cover")
            if c:
                meta_cover[m.get("title_key", "")] = c
            rt = m.get("rating")
            if rt is not None:
                meta_rating[m.get("title_key", "")] = float(rt)
            g = m.get("genres")
            if g:
                meta_genres[m.get("title_key", "")] = g
            t = m.get("type")
            if t:
                meta_type[m.get("title_key", "")] = str(t)
            o = m.get("origin")
            if o:
                meta_origin[m.get("title_key", "")] = str(o)
    except Exception as e:
        logger.warn("build_whitelist_mapped_row: meta load failed", err=str(e)[:120])
    try:
        _dh_data = dh_rows.data if (dh_rows and getattr(dh_rows, "data", None)) else []
        for d in _dh_data:
            tk = d.get("title_key", "")
            ts = d.get("sent_at") or ""
            if tk and (tk not in last_notified or ts > last_notified[tk]):
                last_notified[tk] = ts
    except Exception as e:
        logger.warn("build_whitelist_mapped_row: dispatch_history load failed", err=str(e)[:120])
    return (
        rc_map,
        meta_desc,
        meta_cover,
        meta_rating,
        meta_genres,
        meta_type,
        meta_origin,
        last_notified,
    )
