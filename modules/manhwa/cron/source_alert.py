"""Source health transition alerts.

Sends a Discord alert to the admin/guild channel when a scraping source
changes health state (healthy -> degraded, or degraded -> healthy), plus a
re-escalation every ESCALATE_EVERY consecutive failures so a long outage
isn't silent after the first alert.

Grounded in real probe data (HTTP status / consecutive_failures) — no
guessing. Anti-spam: only fires on a genuine transition or an escalation
milestone, never on every cron tick.
"""
from __future__ import annotations

from modules.manhwa.cron.notify_common import send_alert
from modules.manhwa.logger import get_logger

logger = get_logger("source-alert")

# Re-alert every N consecutive failures during a sustained outage.
ESCALATE_EVERY = 5


def _send(content: str) -> None:
    send_alert(content, log_label="source alert")


def alert_source_transitions(prev_map: dict, curr_map: dict) -> None:
    """Compare previous (DB) vs current (probe) source health and alert on
    state transitions / escalations.

    prev_map / curr_map: {source: {"status": "healthy"|"degraded",
                                    "consecutive_failures": int,
                                    "last_error": str|None,
                                    "response_time_ms": int}}
    """
    for src, curr in (curr_map or {}).items():
        prev = (prev_map or {}).get(src) or {}
        prev_status = (prev.get("status") or "healthy").lower()
        curr_status = (curr.get("status") or "healthy").lower()
        curr_fails = int(curr.get("consecutive_failures") or 0)
        rt = curr.get("response_time_ms")
        err = curr.get("last_error") or "unknown"

        # Hysteresis: only alert on a real sustained change, not a single
        # transient blip. A lone healthy->degraded (1 failure) or
        # degraded->healthy (1 recovery) just flaps the admin channel every
        # cron tick. Require the previous state to have been unhealthy for at
        # least 2 consecutive failures before alerting either direction.
        _MIN_ALERT_FAILS = 2
        _prev_fails = int(prev.get("consecutive_failures") or 0)

        # healthy -> degraded: only after sustained failures (not a single blip)
        if prev_status == "healthy" and curr_status == "degraded" and curr_fails >= _MIN_ALERT_FAILS:
            _send(f"🔴 **{src} DEGRADED** — {err} (probe failed {curr_fails}x). Scraper will "
                   f"skip this source until it recovers; other sources still run.")
            logger.warn("source transition healthy->degraded", src=src, err=err, fails=curr_fails)

        # degraded -> healthy: recovery (only meaningful if it was sustained)
        elif prev_status == "degraded" and curr_status == "healthy" and _prev_fails >= _MIN_ALERT_FAILS:
            rt_txt = f", {rt}ms" if rt is not None else ""
            _send(f"🟢 **{src} RECOVERED** — 200 OK{rt_txt}. Back in the scrape rotation.")
            logger.info("source transition degraded->healthy", src=src)

        # still degraded: escalate every ESCALATE_EVERY consecutive failures
        elif curr_status == "degraded" and curr_fails > 0 and curr_fails % ESCALATE_EVERY == 0:
            _send(f"⚠️ **{src} still DEGRADED** — {curr_fails} consecutive failures "
                   f"({err}). Manual check may be needed.")
            logger.warn("source degraded escalation", src=src, consecutive=curr_fails)
