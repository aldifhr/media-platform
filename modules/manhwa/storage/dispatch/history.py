"""Dispatch history reads — already-sent / already-claimed lookups."""
from __future__ import annotations

from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger

logger = get_logger("storage:dispatch")


def _already_dispatched(urls: list[str]) -> set[str]:
    """Check dispatch_history for already-sent chapter URLs (parity batchCheckDispatchedChapters)."""
    if not urls:
        return set()
    try:
        res = (
            get_supabase()
            .table("dispatch_history")
            .select("chapter_url")
            .in_("chapter_url", urls)
            .execute()
        )
        return {r["chapter_url"] for r in (res.data or [])}
    except Exception as e:
        logger.error("check dispatch_history failed", exc=e)
        return set()


def _claimed_urls(urls: list[str]) -> set[str]:
    """URLs already claimed in dispatch_claims (non-expired)."""
    if not urls:
        return set()
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        res = (
            get_supabase()
            .table("dispatch_claims")
            .select("chapter_url")
            .in_("chapter_url", urls)
            .gte("expires_at", now)
            .execute()
        )
        return {r["chapter_url"] for r in (res.data or [])}
    except Exception as e:
        if "does not exist" in str(e):
            return set()
        logger.error("check dispatch_claims failed", exc=e)
        return set()


def _already_dispatched_fcfs(fcfs_keys: list[str]) -> set[str]:
    """Subset of fcfs_keys permanently recorded in dispatch_history.

    History-only (no live claims) — the "already notified" set used by the
    dispatch runner and DispatchService. ``_claimed_fcfs_keys`` adds the
    live-claim check on top.
    """
    if not fcfs_keys:
        return set()
    try:
        res = (
            get_supabase()
            .table("dispatch_history")
            .select("fcfs_key")
            .in_("fcfs_key", fcfs_keys)
            .execute()
        )
        return {r["fcfs_key"] for r in (res.data or []) if r.get("fcfs_key")}
    except Exception as e:
        logger.warn("fcfs history check failed", err=str(e)[:120])
        return set()


def _claimed_fcfs_keys(fcfs_keys: list[str]) -> set[str]:
    """Return subset of fcfs_keys already in dispatch_history or live claims.

    Permanent (dispatch_history) + live (dispatch_claims) so a concurrent
    runner's claim also blocks. Lives in storage (queries dispatch tables);
    services.fcfs re-exports it as claimed_fcfs_keys.
    """
    if not fcfs_keys:
        return set()
    claimed: set[str] = set()
    try:
        res = get_supabase().table("dispatch_history").select("fcfs_key").in_("fcfs_key", fcfs_keys).execute()
        claimed |= {r["fcfs_key"] for r in (res.data or []) if r.get("fcfs_key")}
    except Exception as e:
        logger.warn("fcfs claimed check dispatch_history failed", err=str(e)[:120])
    try:
        res2 = get_supabase().table("dispatch_claims").select("fcfs_key").in_("fcfs_key", fcfs_keys).execute()
        claimed |= {r["fcfs_key"] for r in (res2.data or []) if r.get("fcfs_key")}
    except Exception as e:
        logger.warn("fcfs claimed check dispatch_claims failed", err=str(e)[:120])
    return claimed
