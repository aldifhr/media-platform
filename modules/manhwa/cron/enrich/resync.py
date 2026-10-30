"""Separate, low-frequency cron

Why this exists (decoupled from the per-10-min chapter fetch):
- The rss-fetch pipeline scrapes new chapters + inserts them. Running the
  full per-series enrich (rating/status/genres/description via source APIs)
  inside that hot path is what made a run ~100s and occasionally blow
  the _SOURCE_TIMEOUT budget.
- Collect now already carries rating/cover/description from the list response,
  so the fetch path is fast (~30s) WITHOUT hitting the source APIs.
- This job runs on its own schedule (e.g. every 15-30 min via cron action
  "enrich") and patiently re-enriches every recent_chapters row in the 24h
  window: it fills status + genres (and refreshes rating/description/cover)
  from series_meta / source APIs, with a polite inter-fetch delay so we stay
  under source 429 / Cloudflare 403 thresholds. No timeout pressure.

It is idempotent: re-running only refreshes rows, never duplicates.

This mirrors the design of series_meta_sync.py (static series metadata) but
operates on the recent_chapters feed rows directly so the Discord/RSS embed
shows complete metadata without blocking the scrape.
"""
from __future__ import annotations

import sys
import time

from modules.manhwa.core.time import cutoff_iso
from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger
from modules.manhwa.storage import recent_chapters as rc_store

logger = get_logger("cron:enrich-resync")

# Patience: not on the hot path, so a slow polite cadence is fine.
_INTER_FETCH_DELAY = 0.5
_WINDOW_HOURS = 24

def enrich_recent_chapters(limit: int = 200, miss_only: bool = False) -> dict:
    """Re-enrich recent_chapters rows in the 24h window.

    Reads rows in chunks (default 200), runs enrich() per chunk, upserts meta.
    Chunking keeps each run short (~10-30s) so the worker is never blocked
    for minutes — other cron jobs (enrich-missing) stay responsive.

    miss_only=True -> static-data mode: only rows missing description/rating/genres.
    """
    from modules.manhwa.cron.enrich import enrich

    sb = get_supabase()
    start = time.time()

    try:
        cutoff = cutoff_iso(_WINDOW_HOURS)
        if miss_only:
            # Static-data cron: only rows missing description/rating/genres.
            # Use raw SQL via psycopg2 so we filter server-side (PostgREST
            # .or() with IS NULL is awkward). Fall back to Supabase fetch + python filter if conn fails.
            try:
                from modules.manhwa.db import get_conn as _gc, put_conn as _pc
                _conn = _gc()
                _cur = _conn.cursor()
                _cur.execute(
                    """
                    SELECT * FROM recent_chapters
                    WHERE updated_time >= %s
                      AND (
                        description IS NULL OR description = ''
                        OR rating IS NULL OR rating = 0
                        OR genres IS NULL OR genres::text = '[]' OR genres::text = 'null'
                      )
                    ORDER BY updated_time DESC
                    LIMIT %s
                    """,
                    (cutoff, limit),
                )
                rows = [dict(r) for r in _cur.fetchall()] if _cur.description else []
                _pc(_conn)
            except Exception as _e:
                logger.debug("enrich_resync miss_only fallback to Supabase", err=str(_e)[:120])
                rows = (
                    sb.table("recent_chapters")
                    .select("*")
                    .gte("updated_time", cutoff)
                    .limit(limit)
                    .execute()
                    .data
                    or []
                )
                # python-side miss filter
                def _is_miss(r: dict) -> bool:
                    d = r.get("description")
                    ra = r.get("rating")
                    g = r.get("genres")
                    if not d:
                        return True
                    if ra in (None, 0, "0", ""):
                        return True
                    if not g or g == [] or str(g) in ("[]", "null", ""):
                        return True
                    return False
                rows = [r for r in rows if _is_miss(r)]
        else:
            rows = (
                sb.table("recent_chapters")
                .select("*")
                .gte("updated_time", cutoff)
                .limit(limit)
                .execute()
                .data
                or []
            )
    except Exception as e:
        logger.error("enrich_resync: list failed", exc=e)
        return {"ok": False, "error": str(e)[:160]}

    if not rows:
        return {"ok": True, "updated": 0, "failed": 0, "duration": 0.0, "note": "no rows in window" + (" (miss_only)" if miss_only else "")}

    # Map DB rows -> item dicts enrich() understands.
    items = [rc_store._row_to_item(r) for r in rows]
    try:
        enriched = enrich(items, persist_cache=True, skip_api=False)
    except Exception as e:
        logger.error("enrich_resync: enrich failed", exc=e)
        return {"ok": False, "error": str(e)[:160]}

    # Upsert only the meta columns back, keyed by chapter_url (unique per row).
    # NOTE: use raw psycopg2 UPDATE, NOT the Supabase upsert — the db_adapter
    # turns a partial-column upsert dict into a full-row INSERT, which violates
    # NOT-NULL constraints on title_key/chapter/ etc. A targeted UPDATE only
    # touches the meta columns and is safe.
    # CRITICAL: only SET a column when enrich produced a non-empty value —
    # enrich() returns '' for fields it couldn't fill (e.g. status for sources
    # whose list response has no status), and a blind UPDATE would OVERWRITE a
    # previously-good value with ''. We preserve existing values by skipping
    # empty ones.
    from modules.manhwa.db import get_conn, put_conn
    import json as _json
    conn = get_conn()
    cur = conn.cursor()
    updated = 0
    failed = 0
    for it in enriched:
        cu = it.get("chapter_url")
        if not cu:
            failed += 1
            continue
        _sets: list[str] = []
        _vals: list = []
        _rating = it.get("rating")
        if _rating not in (None, 0, ""):
            if isinstance(_rating, (int, float)):
                _sets.append("rating=%s"); _vals.append(float(_rating))
            else:
                # enrich() may return string ratings (e.g. "9", "7.67") — try parse
                try:
                    _parsed = float(str(_rating).strip())
                    if _parsed > 0:
                        _sets.append("rating=%s"); _vals.append(_parsed)
                except (ValueError, TypeError):
                    if str(_rating).strip().lower() == "rating":
                        logger.debug("enrich_resync: skip header rating", chapter_url=cu)
                    else:
                        logger.debug("enrich_resync: skip non-numeric rating", chapter_url=cu, rating=str(_rating)[:40])
        _cover = it.get("cover")
        if _cover:
            _sets.append("cover=%s"); _vals.append(str(_cover))
        _genres = it.get("genres")
        if _genres:
            _sets.append("genres=%s"); _vals.append(_json.dumps(_genres))
        _desc = it.get("description")
        if _desc:
            _sets.append("description=%s"); _vals.append(str(_desc))
        _origin = it.get("origin")
        if _origin:
            _sets.append("origin=%s"); _vals.append(str(_origin))
        if not _sets:
            # Nothing to update for this row — skip (don't clobber).
            continue
        try:
            cur.execute(
                f"UPDATE recent_chapters SET {', '.join(_sets)} WHERE chapter_url=%s",
                tuple(_vals) + (cu,),
            )
            updated += 1
        except Exception as e:
            err_s = str(e).lower()
            if "already closed" in err_s or "cursor" in err_s and "closed" in err_s:
                logger.debug("enrich_resync: abort — pool closed during shutdown", cu=cu[:40])
                failed += 1
                try:
                    conn.rollback()
                except Exception:
                    pass
                put_conn(conn)
                return {"ok": False, "error": "pool closed — aborted mid-batch", "updated": updated, "failed": failed}
            logger.debug("enrich_resync: update failed", cu=cu[:60], err=str(e)[:120])
            failed += 1
        time.sleep(_INTER_FETCH_DELAY)
    try:
        conn.commit()
    except Exception as e:
        err_s = str(e).lower()
        if "already closed" not in err_s:
            logger.debug("enrich_resync: commit failed", err=str(e)[:120])
    try:
        put_conn(conn)
    except Exception:
        pass

    duration = round(time.time() - start, 1)
    stats = {
        "ok": True,
        "rows_in_window": len(rows),
        "updated": updated,
        "failed": failed,
        "duration": duration,
        "miss_only": miss_only,
    }
    logger.info("enrich resync done", **stats)
    return stats

def enrich_stale_series_meta(stale_days: int = 7, limit: int = 50) -> dict:
    """Weekly refresh: re-fetch series_meta rows older than stale_days.

    Checks static data (rating/genres/description/cover/type) for drift.
    Only updates series_meta when upstream actually changed, so 403/429
    budget is spent on ~50 stale series/week, not the full 24h window.
    """
    from datetime import datetime, timezone, timedelta

    sb = get_supabase()
    start = time.time()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()

    try:
        # Server-side filter via psycopg2 (PostgREST .lt on timestamptz flaky)
        try:
            from modules.manhwa.db import get_conn as _gc2, put_conn as _pc2
            _conn = _gc2()
            _cur = _conn.cursor()
            _cur.execute(
                """
                SELECT title_key, source, rating, genres, description, cover, type, updated_at
                FROM series_meta
                WHERE updated_at IS NULL OR updated_at < %s
                ORDER BY updated_at ASC NULLS FIRST
                LIMIT %s
                """,
                (cutoff, limit),
            )
            rows = [dict(r) for r in _cur.fetchall()] if _cur.description else []
            _pc2(_conn)
        except Exception:
            # Fallback: Supabase client
            rows = (
                sb.table("series_meta")
                .select("title_key, source, updated_at")
                .lt("updated_at", cutoff)
                .order("updated_at", desc=False)
                .limit(limit)
                .execute()
                .data
                or []
            )
    except Exception as e:
        logger.error("enrich_stale: list failed", exc=e)
        return {"ok": False, "error": str(e)[:160]}

    if not rows:
        return {"ok": True, "updated": 0, "checked": 0, "duration": 0.0, "note": f"no stale >{stale_days}d"}

    # Resolve slug per series via recent_chapters series_url
    from modules.manhwa.cron.series_meta_sync import _fetch_meta, _slug_for

    updated = 0
    checked = 0
    failed = 0
    for r in rows:
        tk = str(r.get("title_key") or "").strip()
        src = str(r.get("source") or "").strip()
        if not tk or not src:
            continue
        slug = _slug_for(src, tk)
        if not slug:
            failed += 1
            continue
        checked += 1
        try:
            meta = _fetch_meta(src, slug)
        except Exception:
            meta = {}
        if not meta:
            failed += 1
            time.sleep(1.0)
            continue
        # Only UPSERT if something actually changed (avoid bumping updated_at needlessly)
        old_desc = (r.get("description") or "").strip()
        old_rating = str(r.get("rating") or "")
        old_type = str(r.get("type") or "")
        new_desc = (meta.get("description") or "").strip()
        new_rating = str(meta.get("rating") or "")
        new_type = str(meta.get("type") or "")
        if old_desc == new_desc and old_rating == new_rating and old_type == new_type:
            time.sleep(1.0)
            continue
        try:
            sb.table("series_meta").upsert(
                {
                    "title_key": tk,
                    "source": src,
                    "rating": meta.get("rating"),
                    "genres": meta.get("genres") or [],
                    "description": meta.get("description") or "",
                    "cover": meta.get("cover"),
                    "type": meta.get("type"),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="title_key,source",
            ).execute()
            updated += 1
        except Exception as e:
            logger.warn("enrich_stale: upsert failed", tk=tk, src=src, err=str(e)[:120])
            failed += 1
        time.sleep(1.0)

    duration = round(time.time() - start, 1)
    stats = {"ok": True, "checked": checked, "updated": updated, "failed": failed, "duration": duration, "stale_days": stale_days}
    logger.info("enrich stale done", **stats)
    return stats


if __name__ == "__main__":
    import os
    os.environ.setdefault("PYTHONPATH", ".")
    res = enrich_recent_chapters()
    print(res, file=sys.stderr)

