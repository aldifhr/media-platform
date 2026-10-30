"""Backfill helpers extracted from app/cron/enrich/__init__.py.

These operate on dispatch items + storage, not on cron orchestration, so they
live in the services layer. `cron.enrich` re-exports them for backward compat.
"""
from __future__ import annotations

from modules.manhwa.core.time import RECENT_WINDOW_HOURS, cutoff_dt, parse_iso_utc
from modules.manhwa.logger import get_logger

logger = get_logger("services:dispatch_backfill")


def _split_send_backfill(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Strict-24h split: items with a real (non-backfill) updated_time within
    24h go to Discord; HTML-backlog items (flagged html_backlog, no real
    timestamp) go to silent backfill.
    """
    from datetime import datetime, timezone
    from modules.manhwa.services.fcfs import fcfs_key

    # Dedup by FCFS key, but keep the SUBSCRIBED copy when a title+chapter
    # arrives from both a raw and a whitelisted source. Keeping the first copy
    # used to drop the subscribed one, so the chapter landed on RAW and the
    # whitelist feed got nothing. `origin` is a country code (KR/CN/JP), never
    # "whitelist" — the subscription flag is `subscribed` (set by
    # split_raw_and_whitelisted), with isWhitelisted as the read-side fallback.
    def _is_subscribed(it: dict) -> bool:
        return bool(it.get("subscribed")) or bool(it.get("isWhitelisted"))

    _best: dict[str, dict] = {}
    _order: list[str] = []
    for it in items:
        _k = fcfs_key(it.get("title") or "", it.get("chapter") or "")
        if _k not in _best:
            _best[_k] = it
            _order.append(_k)
            continue
        _cur = _best[_k]
        if _is_subscribed(it) and not _is_subscribed(_cur):
            _best[_k] = it
    items = [_best[k] for k in _order]

    cutoff = cutoff_dt(RECENT_WINDOW_HOURS)
    to_send: list[dict] = []
    to_backfill: list[dict] = []
    seen_fcfs: set[str] = set()
    for it in items:
        norm = fcfs_key(it.get("title") or "", it.get("chapter") or "")
        if norm in seen_fcfs:
            if it.get("html_backlog"):
                to_backfill.append(it)
            continue
        seen_fcfs.add(norm)
        if it.get("html_backlog"):
            to_backfill.append(it)
            continue
        ts_raw = it.get("updated_time") or ""
        in_window = False
        if ts_raw:
            # Accept both str (RSS/JSON) and datetime (DB row via _row_to_item)
            if isinstance(ts_raw, datetime):
                ts = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=timezone.utc)
            else:
                ts = parse_iso_utc(ts_raw)
            if ts is not None:
                in_window = ts >= cutoff
        if in_window:
            to_send.append(it)
        elif ts_raw:
            to_backfill.append(it)
        else:
            if it.get("subscribed") or it.get("isWhitelisted"):
                it["window_status"] = "fresh_whitelist"
                to_send.append(it)
            else:
                it["window_status"] = "unknown_time"
                # .get() returns the stored None when the key is present but
                # null, so this must coerce rather than slice the default.
                _t = it.get("title") or ""
                _c = it.get("chapter") or ""
                logger.warn(
                    "enrich: null updated_time, non-whitelist source — skipped",
                    title=_t[:40],
                    chapter=_c[:20],
                    source=it.get("source") or "",
                    url=(it.get("url") or "")[:120],
                )
    return to_send, to_backfill


def mark_history_only(urls: list[str], title_keys: list[str]) -> int:
    """Record backlog chapter URLs as already-seen in dispatch_claims."""
    if not urls:
        return 0
    from datetime import datetime, timezone, timedelta
    from modules.manhwa.db import get_supabase

    expires = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    rows = [
        {"chapter_url": u, "title_key": tk or "unknown", "expires_at": expires}
        for u, tk in zip(urls, title_keys)
        if u
    ]
    if not rows:
        return 0
    try:
        get_supabase().table("dispatch_claims").upsert(
            rows, on_conflict="chapter_url"
        ).execute()
        return len(rows)
    except Exception as e:
        logger.warn("mark_history_only failed", err=str(e))
        return 0


def backfill_dispatch_history(items: list[dict], instance_id: str) -> int:
    """Silently record backlog chapter URLs as already-dispatched so they're
    never notified later. Used for HTML-scraped backlog chapters."""
    urls = [it.get("url") or it.get("chapter_url") for it in items if (it.get("url") or it.get("chapter_url"))]
    tks = [it.get("title_key", "unknown") for it in items if (it.get("url") or it.get("chapter_url"))]
    if not urls:
        return 0
    return mark_history_only(urls, tks)
