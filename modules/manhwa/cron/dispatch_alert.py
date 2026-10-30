"""Dispatch failure alerts.

Alerts the admin/guild channel when failed Discord sends accumulate within a
window. Anti-spam: at most one alert per COOLDOWN_MIN, and only when the
failure count crosses the threshold — a single blip never pages anyone.
"""
from __future__ import annotations

import time

from modules.manhwa.cron.notify_common import send_alert
from modules.manhwa.logger import get_logger

logger = get_logger("dispatch-alert")

FAILED_THRESHOLD = 5        # failures within the window before alerting
COOLDOWN_MIN = 30           # min minutes between alerts
_WINDOW_MIN = 15            # failure window

_last_alert_ts: float = 0.0


def check_and_alert_failed_dispatches() -> None:
    """Called after each dispatch pass. Reads failed_dispatches rows created in
    the last _WINDOW_MIN; alerts if >= FAILED_THRESHOLD and cooldown elapsed."""
    global _last_alert_ts
    try:
        from modules.manhwa.db import get_supabase
        sb = get_supabase()
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - _WINDOW_MIN * 60))
        rows = (
            sb.table("failed_dispatches")
            .select("id, chapter_url, error_message, created_at")
            .gte("created_at", cutoff)
            .limit(100)
            .execute()
            .data or []
        )
        if len(rows) < FAILED_THRESHOLD:
            return
        now = time.monotonic()
        if now - _last_alert_ts < COOLDOWN_MIN * 60:
            return
        _last_alert_ts = now

        sample = rows[0]
        err = str(sample.get("error_message") or "unknown")[:120]
        content = (
            f"🚨 **Dispatch failures: {len(rows)} in the last {_WINDOW_MIN}m**\n"
            f"Sample: `{(sample.get('chapter_url') or '')[:80]}` — {err}\n"
            f"Check `/api/failed-dispatches` and retry with `/api/dispatches`."
        )

        if not send_alert(content, log_label="dispatch-failure"):
            return
        logger.warn("dispatch failure alert sent", count=len(rows))
    except Exception as e:
        logger.warn("dispatch-failure alert check failed", err=str(e)[:160])
