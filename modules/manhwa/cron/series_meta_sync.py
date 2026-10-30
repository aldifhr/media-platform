"""Separate, low-frequency cron that syncs per-series static metadata.

Why this exists (decoupled from the per-minute chapter collect):
- rating / description / genres / type are STATIC per series — they do not
  change per chapter, so re-fetching them on every chapter-scrape is waste.
- The per-minute collect pipeline now runs with fetch_meta=False (fast path:
  just scrape new chapters + insert). RSS joins series_meta, so chapters
  still show rating/desc even though the row itself has no meta columns.
- This job runs on its own schedule (e.g. every 6h via cron action
  "sync-meta") and patiently fetches + upserts series_meta for every distinct
  (title_key, source), with a long inter-fetch delay so we stay well under
  shinigami's 429 threshold. No timeout pressure.

It is idempotent: re-running only refreshes rows, never duplicates.

"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger

logger = get_logger("cron:series-meta-sync")

# Patience: this job is not on the hot path, so we can afford a slow, polite
# cadence that never trips upstream rate limits.
_INTER_FETCH_DELAY = 1.0
_MAX_PER_RUN = 2000  # safety cap; distinct series is ~85 so we never hit this

def _fetch_meta(source: str, sid: str) -> dict:
    """Fetch one series' meta via the scraper (same path collect used)."""
    if source == "shinigami":
        from modules.manhwa.sources import shinigami as _sh
        return _sh.get_shinigami_series_meta(sid) or {}
    return {}

def _slug_for(source: str, title_key: str) -> str | None:
    """Resolve the source-specific id/slug for a (title_key, source) pair.

    series_meta only stores the title_key + source, not the upstream slug.
    We look it up from recent_chapters (series_url last path segment) which
    collect already populated. N+1 version kept for compat; bulk path in sync_series_meta avoids it.
    """
    sb = get_supabase()
    try:
        rows = (
            sb.table("recent_chapters")
            .select("series_url")
            .eq("title_key", title_key)
            .eq("source", source)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            su = (rows[0].get("series_url") or "").rstrip("/")
            return su.split("/")[-1] if su else None
    except Exception:
        pass
    return None

def sync_series_meta(limit: int = _MAX_PER_RUN) -> dict:
    """Fetch + upsert series_meta for every distinct (title_key, source).

    Returns a stats dict for logging. Safe to run repeatedly.
    """
    sb = get_supabase()
    start = time.time()

    # Distinct series — bulk fetch series_url sekali (hindari N+1 _slug_for per series)
    try:
        rows = (
            sb.table("recent_chapters")
            .select("title_key, source, series_url")
            .limit(5000)
            .execute()
            .data
            or []
        )
    except Exception as e:
        logger.error("sync_series_meta: list failed", exc=e)
        return {"ok": False, "error": str(e)[:160]}

    # Build distinct map (title_key, source) -> slug (dari series_url)
    slug_map: dict[tuple[str, str], str] = {}
    for r in rows:
        tk = str(r.get("title_key") or "").strip()
        src = str(r.get("source") or "").strip()
        if not tk or not src:
            continue
        key = (tk, src)
        if key in slug_map:
            continue
        su = (r.get("series_url") or "").rstrip("/")
        slug = su.split("/")[-1] if su else ""
        # fallback: the title_key doubles as the slug when series_url is absent
        if not slug:
            slug = tk
        slug_map[key] = slug

    try:
        wl_rows = sb.table("whitelist").select("title_key, source, series_url").limit(5000).execute().data or []
        for r in wl_rows:
            tk = str(r.get("title_key") or "").strip()
            src = str(r.get("source") or "").strip()
            if not tk or not src:
                continue
            key = (tk, src)
            if key in slug_map:
                continue
            su = (r.get("series_url") or "").rstrip("/")
            slug = su.split("/")[-1] if su else tk
            if not slug:
                slug = tk
            slug_map[key] = slug
    except Exception:
        pass

    series = list(slug_map.items())[:limit]

    updated = 0
    failed = 0
    payloads: list[dict] = []
    for (tk, src), slug in series:
        if not slug:
            failed += 1
            continue
        try:
            meta = _fetch_meta(src, slug)
        except Exception:
            meta = {}
        if meta:
            payloads.append(
                {
                    "title_key": tk,
                    "source": src,
                    "rating": meta.get("rating"),
                    "genres": meta.get("genres") or [],
                    "description": meta.get("description") or "",
                    "cover": meta.get("cover"),
                    "type": meta.get("type"),
                    "origin": meta.get("origin") or "",
                    # Real UTC timestamp — a literal "now()" is bound as text
                    # by PostgREST and breaks every staleness comparison.
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        else:
            failed += 1
        time.sleep(_INTER_FETCH_DELAY)

    # Bulk upsert (1 round-trip, bukan N)
    if payloads:
        try:
            sb.table("series_meta").upsert(payloads, on_conflict="title_key,source").execute()
            updated = len(payloads)
        except Exception as e:
            logger.warn("sync_series_meta: bulk upsert failed", err=str(e)[:160])
            # fallback per-row
            for p in payloads:
                try:
                    sb.table("series_meta").upsert(p, on_conflict="title_key,source").execute()
                    updated += 1
                except Exception as se:
                    logger.warn("sync_series_meta: upsert failed", tk=p.get("title_key"), src=p.get("source"), err=str(se)[:120])
                    failed += 1
            updated = updated - failed if updated else 0

    try:
        from modules.manhwa.db import q as _q
        _q("REFRESH MATERIALIZED VIEW CONCURRENTLY v_series")
    except Exception as e:
        logger.warn("v_series refresh skipped/failed (view may not be materialized yet)", err=str(e)[:160])

    duration = round(time.time() - start, 1)
    stats = {
        "ok": True,
        "distinct_series": len(series),
        "updated": updated,
        "failed": failed,
        "duration": duration,
    }
    logger.info("series_meta sync done", **stats)
    return stats

if __name__ == "__main__":
    import os
    os.environ.setdefault("PYTHONPATH", ".")
    res = sync_series_meta()
    print(res, file=sys.stderr)
