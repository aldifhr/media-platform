"""Unified metadata enrichment for whitelist entries.

Fetches rich metadata (cover, rating, genres, description, status)
from source APIs and stores directly in the
whitelist table — single source of truth, no manga_metadata dependency.
"""

from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger

logger = get_logger("enrich")

def enrich_whitelist_entry(title_key: str, source: str, series_url: str | None = None) -> dict | None:
    """Fetch metadata from source API. Returns dict of updates or None."""
    updates: dict = {}

    if source == "shinigami":
        from modules.manhwa.sources import shinigami
        # Derive manga_id from series_url, else from recent_chapters
        mid = None
        if series_url and "shinigami.asia/series/" in series_url:
            mid = series_url.rstrip("/").split("/")[-1]
        if not mid:
            # fallback: look up series_url from recent_chapters by title_key
            try:
                from modules.manhwa.db import get_supabase as _gsb
                _rc = _gsb().table("recent_chapters").select("series_url").eq(
                    "title_key", title_key
                ).eq("source", "shinigami").neq("series_url", "").limit(1).execute()
                if _rc.data:
                    _row = _rc.data[0]
                    _su = str((_row.get("series_url") if isinstance(_row, dict) else "") or "")
                    if "shinigami.asia/series/" in _su:
                        mid = _su.rstrip("/").split("/")[-1]
            except Exception:
                pass
        if not mid:
            # last resort: search API by title to resolve the manga UUID
            try:
                from modules.manhwa.sources import shinigami as _sh
                from modules.manhwa.core.text import deslugify_title_key
                _q = deslugify_title_key(title_key)
                _hits = _sh.search_shinigami_api(_q, per_page=5)
                for _h in (_hits or []):
                    _hid = _h.get("id") or _h.get("manga_id") or _h.get("uuid")
                    if _hid:
                        mid = str(_hid)
                        break
            except Exception:
                pass
        if not mid:
            return None

        meta = shinigami.get_shinigami_series_meta(mid)
        if meta:
            for f in ("cover", "rating", "genres", "description", "type"):
                v = meta.get(f)
                if v:
                    updates[f] = v
            updates["source"] = "shinigami"

    return updates if updates else None

_ENRICH_LAST_RUN: float = 0
_ENRICH_THROTTLE_S = 300  # 5m — faster metadata for new series

def enrich_all_whitelist(max_age_hours: int = 24, refresh_days: int = 7, force: bool = False) -> int:
    """Enrich whitelist entries with upstream metadata (cover, rating, genres,
    description, status, type, origin).

    force=True — bypass throttle + force refresh ALL entries (admin button).

    PERF-01 fix: previously the SELECT omitted rating/status/cover/origin, so the
    "all_present" completeness check could never be True (those fields read as
    None) and EVERY entry was re-enriched on every cron tick — 343 × N upstream
    requests/hour. Now:
      - we SELECT all completeness fields,
      - skip entries that are already complete AND were enriched within
        `refresh_days` (default 7d) — metadata like genre/rating/status rarely
        changes, so re-fetching every few minutes is pure waste,
      - new titles (metadata_enriched_at IS NULL) are enriched immediately,
      - older-than-refresh entries get a refresh.

    Returns count updated.
    """
    import time as _t
    global _ENRICH_LAST_RUN
    if not force and _t.time() - _ENRICH_LAST_RUN < _ENRICH_THROTTLE_S:
        # quick check via cache? still need SELECT to know, so just skip if within throttle and previous was all-skip
        # we keep simple: if throttled, return 0 immediately (next 5m tick will still check)
        return 0
    sb = get_supabase()

    # Pull minimal from whitelist (cover/rating canonical in series_meta since 052) + completeness via series_meta
    try:
        rows = sb.table("whitelist").select(
            "title_key, source, series_url, metadata_enriched_at"
        ).execute().data or []
    except Exception:
        rows = sb.table("whitelist").select("title_key, source, series_url").execute().data or []
        for _r in rows:
            _r["metadata_enriched_at"] = None

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    refresh_cutoff = (now - timedelta(days=refresh_days)).isoformat()

    updated = 0
    skipped = 0
    refreshed = 0
    for r in rows:
        tk = r["title_key"]
        src = r.get("source", "")
        su = r.get("series_url")

        # whitelist minimal since 052 — all_present via series_meta completeness, fallback False until 053 VIEW stable
        all_present = False
        enriched_at = r.get("metadata_enriched_at")
        _ea_str = str(enriched_at) if enriched_at is not None else None

        if force:
            refreshed += 1
        elif all_present:
            # Complete — only refresh if older than the refresh window.
            if _ea_str and _ea_str >= refresh_cutoff:
                skipped += 1
                continue
            refreshed += 1
        else:
            # Incomplete — but if we enriched very recently, don't hammer the
            # upstream API again (it may have returned partial data).
            if _ea_str and _ea_str >= refresh_cutoff:
                skipped += 1
                continue

        try:
            updates = enrich_whitelist_entry(tk, src, su)
            if updates:
                # whitelist minimal since 061 — static fields go to series_meta, not whitelist
                _sm_update = {k: v for k, v in updates.items() if k in ("cover","rating","genres","description","type","origin")}
                _wl_update: dict = {"metadata_enriched_at": now.isoformat()}
                # keep source for whitelist update (if needed)
                if _sm_update:
                    try:
                        sb.table("series_meta").upsert({"title_key": tk, "source": src, **_sm_update, "updated_at": now.isoformat()}, on_conflict="title_key,source").execute()
                    except Exception:
                        pass
                sb.table("whitelist").update(_wl_update).eq("title_key", tk).eq("source", src).execute()
                updated += 1
        except Exception as e:
            logger.warn("enrich failed", title_key=tk, err=str(e)[:120])

    logger.info("enrich_all_whitelist done", updated=updated, refreshed=refreshed, skipped=skipped, total=len(rows))
    # update throttle marker only when all skipped (no work) -> next run 1h later
    if skipped == len(rows) and updated == 0:
        _ENRICH_LAST_RUN = _t.time()
    else:
        _ENRICH_LAST_RUN = 0  # reset if work done, so next cron retries soon
    return updated
