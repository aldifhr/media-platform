"""Parallel input loaders for the dashboard snapshot.

Extracted from app/api/admin/stats.py — the endpoint and the cron both use
these loaders, so they live in the service layer.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from modules.manhwa.config import settings
from modules.manhwa.core.time import RECENT_WINDOW_HOURS, cutoff_iso
from modules.manhwa.storage import health as health_store
from modules.manhwa.storage import whitelist as wl_store


@dataclass
class SnapshotInputs:
    wl_rows: list
    raw_hm: dict | None
    rc_24h: list
    dh_all: Any
    cron_stats: list
    sb_client: Any


def load_inputs() -> SnapshotInputs:
    """Fetch the five independent dashboard inputs in parallel.

    PostgREST client is sync; running each query in its own thread lets them
    overlap instead of waterfalling.
    """
    from modules.manhwa.storage import recent_chapters as _rc_store
    from modules.manhwa.db import get_supabase as _sb

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_wl = ex.submit(wl_store.load_whitelist)
        f_hm = ex.submit(health_store.load_source_health_map, settings.SOURCE_KEYS)
        f_rc = ex.submit(_rc_store.get_recent_chapters, 24)
        _sb_client = _sb()
        f_dh = ex.submit(
            lambda: (
                _sb_client.table("dispatch_history")
                .select("chapter_url, title_key, source, sent_at, chapter_title")
                .gte("sent_at", cutoff_iso(RECENT_WINDOW_HOURS))
                .order("sent_at", desc=True)
                .limit(500)
                .execute()
            )
        )
        f_cron = ex.submit(
            lambda: (
                _sb_client.table("cron_run_status")
                .select("*")
                .order("created_at", desc=True)
                .limit(200)
                .execute()
            )
        )
        rows = f_wl.result()
        raw_hm = f_hm.result()
        rc_24h = f_rc.result()
        dh_all = f_dh.result()
        cron_stats = (f_cron.result().data) or []
    return SnapshotInputs(
        wl_rows=rows,
        raw_hm=raw_hm,
        rc_24h=rc_24h,
        dh_all=dh_all,
        cron_stats=cron_stats,
        sb_client=_sb_client,
    )


def norm_src(src: str, row: dict) -> dict:
    """Normalize source health to camelCase (same shape as /api/sources/health)."""
    return {
        "name": src,
        "source": src,
        "status": row.get("status", "healthy"),
        "lastCheck": row.get("last_checked_at") or "",
        "lastSuccess": row.get("last_success_at") or "",
        "responseTime": row.get("response_time_ms") or 0,
        "avgResponseTimeMs": row.get("response_time_ms") or 0,
        "successesToday": row.get("successes_today", 0),
        "failuresToday": row.get("failures_today", 0),
        "consecutiveFailures": row.get("consecutive_failures", 0),
        "lastError": row.get("last_error"),
        "disabledUntil": row.get("disabled_until"),
    }
