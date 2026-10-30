"""Recent chapters storage — pure SQL helpers (prune, batch insert, fetch).

Moved from recent_chapters.py to separate orchestration (claim) from storage I/O.
Module-level state (whitelist origin cache, existing-rc cache) lives here so all
helpers share it without crossing module boundaries.
"""
from modules.manhwa.db import get_supabase
from modules.manhwa.config import disabled_sources as _disabled_sources
from modules.manhwa.logger import get_logger
from modules.manhwa.core.sql_fragments import RC_WL_JOIN_ON
from modules.manhwa.core.text import rewrite_shinigami_urls
from modules.manhwa.core.time import cutoff_iso

logger = get_logger("storage:recent-chapters-sql")

from modules.manhwa.storage.recent_chapters_caches import (
    _composite_key,  # noqa: F401 — re-exported for recent_chapters' `import *`
)


def get_trending(hours: int = 24, limit: int = 25) -> list[dict]:
    """Trending series = those releasing the MOST chapters within `hours`.

    Aggregates recent_chapters (flat, per-source) by (title_key, source),
    counts chapters in the window (release velocity = 'naik daun'),
    joins rating from whitelist. Returns newest-first by
    chapter_count then last activity.
    """
    try:
        from modules.manhwa.db import q
        cutoff = cutoff_iso(hours)
        sql = f"""
            SELECT
                rc.title_key,
                rc.source,
                MAX(rc.title) AS title,
                MAX(rc.origin) AS origin,
                MAX(rc.cover) AS cover,
                MAX(rc.series_url) AS series_url,
                COUNT(*) AS chapter_count,
                MAX(rc.updated_time) AS last_update,
                MAX(w.rating) AS rating,
                MAX(w.series_url) AS wl_series_url
            FROM recent_chapters rc
            LEFT JOIN whitelist w
                ON {RC_WL_JOIN_ON}
            WHERE rc.updated_time >= %s
            GROUP BY rc.title_key, rc.source
            ORDER BY chapter_count DESC, last_update DESC
            LIMIT %s
        """
        rows = q(sql, [cutoff, limit]) or []
        out = []
        for r in rows:
            series_url = (r.get("series_url") or r.get("wl_series_url") or "").strip()
            try:
                rating = float(r.get("rating") or 0) or 0.0
            except (ValueError, TypeError):
                rating = 0.0
            out.append({
                "title_key": r.get("title_key", ""),
                "source": r.get("source", ""),
                "title": r.get("title", ""),
                "cover": r.get("cover") or "",
                "series_url": r.get("series_url") or "",
                "chapter_count": int(r.get("chapter_count") or 0),
                "last_update": r.get("last_update") or "",
                "rating": rating,
                "score": round(int(r.get("chapter_count") or 0) + rating / 2.0, 2),
            })
        return out
    except Exception as e:
        logger.error("get_trending failed", exc=e)
        return []

def get_recent_chapters(hours: int = 24) -> list[dict]:
    """Load ALL chapters found within the last `hours` (used by dispatch /
    dashboard callers that need the full set). For web pagination use
    get_recent_chapters_paginated() instead."""
    rows = _fetch_recent_rows(hours=hours, limit=1500, offset=0)
    return [_row_to_item(r) for r in rows]

def get_recent_chapters_paginated(
    page: int = 1, limit: int = 24, hours: int = 24, source: str | None = None
) -> tuple[list[dict], int, int]:
    """Server-side paginated recent chapters for the web RSS feed.

    Only fetches the single page requested (page*limit rows) so infinite
    scroll stays cheap — no loading of all 1500 rows + enriching them just
    to return 24. `source` (if given) is filtered DB-side so pagination
    stays correct per-source. Returns (items, total, total_pages).
    """
    total = _count_recent_rows(hours=hours, source=source)
    total_pages = (total + limit - 1) // limit if limit else 1
    offset = max(0, (page - 1) * limit)
    rows = _fetch_recent_rows(hours=hours, limit=limit, offset=offset, source=source)
    return [_row_to_item(r) for r in rows], total, total_pages

def _count_recent_rows(hours: int = 24, source: str | None = None) -> int:
    try:
        cutoff = cutoff_iso(hours)
        q = (
            get_supabase()
            .table("recent_chapters")
            .select("*", count="exact")
            .gte("updated_time", cutoff)
        )
        if source:
            q = q.eq("source", source)
        else:
            # Exclude disabled sources one at a time — the adapter has neq but
            # no NOT IN, and a disabled source's rows linger for the whole
            # freshness window, so the feed would keep serving them.
            for _off_src in sorted(_disabled_sources()):
                q = q.neq("source", _off_src)
        res = q.execute()
        return res.count or 0
    except Exception as e:
        logger.error("count recent_chapters failed", exc=e)
        return 0

def _fetch_recent_rows(
    hours: int = 24, limit: int = 1500, offset: int = 0, source: str | None = None
) -> list[dict]:
    try:
        cutoff = cutoff_iso(hours)
        q = (
            get_supabase()
            .table("recent_chapters")
            .select("*")
            .gte("updated_time", cutoff)
            # Sort by PK (id) descending — insertion order ≈ chronological,
            # and critically STABLE across pages. Ordering by updated_time
            # alone is unstable (many rows share the same timestamp) which
            # made offset pagination return overlapping rows on page 1/2.
            .order("id", desc=True)
            .limit(limit)
            .offset(offset)
        )
        if source:
            q = q.eq("source", source)
        else:
            # Exclude disabled sources one at a time — see config.disabled_sources().
            # Without this the feed serves rows from a source that was turned
            # off, until they age out of the window.
            for _off_src in sorted(_disabled_sources()):
                q = q.neq("source", _off_src)
        res = q.execute()
        return res.data or []  # type: ignore
    except Exception as e:
        logger.error("fetch recent_chapters failed", exc=e)
        return []

def _row_to_item(r: dict) -> dict:
    item = {
        "id": r.get("id"),
        "title": r.get("title", ""),
        "title_key": r.get("title_key", ""),
        "chapter": str(r.get("chapter") or ""),
        "chapter_num": r.get("chapter_num") or 0,
        "url": r.get("chapter_url") or r.get("url", ""),
        "chapter_url": r.get("chapter_url") or r.get("url", ""),
        "source": r.get("source", ""),
        # Return a short same-origin cover ref (BE-3c) instead of the raw
        # (often very long MinIO) URL. The DB keeps the raw URL; this only
        # rewrites on read so the backend proxy resolves it server-side.
        "cover": r.get("cover") or "",
        "series_url": r.get("series_url") or "",
        "updated_time": r.get("updated_time") or "",
        "description": r.get("description") or "",
        # Carry rating + genres through to dispatch embeds. Previously dropped
        # here, so all sources via the claim path rendered empty
        # rating/genre in Discord. DB stores them; the embed builder consumes them.
        "rating": r.get("rating") or "",
        "genres": r.get("genres") or [],
        "origin": r.get("origin") or "",
        "type": r.get("type") or "",  # <-- ADDED for origin derivation fallback
    }
    return rewrite_shinigami_urls(item, ("url", "chapter_url", "cover"))
