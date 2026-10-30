"""Dispatch starvation watchdog.

Answers two questions the existing alerts do not:

  1. Is dispatch RUNNING at all? A source alert covers "shinigami is failing",
     but not "the cron worker stopped" or "the queue wedged" — the July outage
     was exactly that: every source reported HEALTHY for 7 hours while no
     notification left the box, and it was only noticed by eye.

  2. Is anything ELIGIBLE but UNDELIVERED? Even a running dispatcher can drop a
     chapter (a claim held too long, a ledger write that failed). Comparing the
     eligible set against the FCFS ledger catches that regardless of cause.

Both are grounded in the same `fcfs_key` the dispatcher itself uses, so this
cannot disagree with the pipeline about what "sent" means. An earlier ad-hoc
check compared against a hand-rolled key and produced false positives — that
mistake is the reason this uses the real function.

Anti-spam: one alert per condition per cooldown window, tracked in Redis, and
the state resets as soon as the condition clears so the next genuine incident
still alerts.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from modules.manhwa.core.sql_fragments import RC_WL_JOIN_ON
from modules.manhwa.cron.notify_common import send_alert
from modules.manhwa.logger import get_logger

logger = get_logger("dispatch-watchdog")

# No successful dispatch for this long => something is wrong.
# The scheduler enqueues `update` every 120s, so 30 minutes is ~15 missed
# cycles: comfortably beyond a transient hiccup, well inside "the user would
# have noticed".
STARVE_AFTER_MIN = 30

# An eligible chapter older than this without a ledger entry is treated as
# dropped. The dispatcher runs every 2 minutes, so 60 is generous slack.
# Raised from 15 because a chapter that just entered the whitelist (e.g. user
# subscribes a new source) can take up to an hour to be picked up by the
# rotating walk — that is normal, not a failure.
UNDELIVERED_AFTER_MIN = 60

# Send the UNDELIVERED alert? Off by default.
#
# Why: it paged 5x in 24h on chapters that were in fact delivered. The
# definition ("whitelisted, inside 24h window, no dispatch_history row")
# has too many ways to disagree with reality — a source renumbering, a
# backlog being drained, a chapter that is deliberately held back. Every
# occurrence needed manual DB work to disprove, which is a worse deal
# than the outages it was built to catch: dispatch_history showed the
# chapters arriving minutes after each alert.
#
# The number is still computed and logged every run, so the data is
# there if it is ever needed. Set ALERT_UNDELIVERED=1 to re-enable.
ALERT_UNDELIVERED = os.environ.get("ALERT_UNDELIVERED", "0") == "1"

# Re-alert at most this often while a condition persists.
COOLDOWN_MIN = 60

_STATE_KEY = "beag:dispatch_watchdog:alerted"


def _redis():
    from modules.manhwa.tasks.queue import _get_redis

    return _get_redis()


def _send(content: str) -> None:
    send_alert(content, log_label="dispatch alert")


def _should_alert(condition: str) -> bool:
    """True when this condition has not alerted inside the cooldown."""
    try:
        r = _redis()
        if r.get(f"{_STATE_KEY}:{condition}"):
            return False
        r.setex(f"{_STATE_KEY}:{condition}", COOLDOWN_MIN * 60, "1")
        return True
    except Exception:
        # Redis unavailable: prefer alerting (a duplicate beats a silent outage).
        return True


def _clear_alert_state(condition: str) -> None:
    """Drop the cooldown once the condition clears, so the NEXT incident alerts
    immediately instead of being suppressed by the previous one's window."""
    try:
        _redis().delete(f"{_STATE_KEY}:{condition}")
    except Exception:
        pass


def check_dispatch_starvation() -> dict:
    """Alert when dispatch has not succeeded recently, or chapters are stuck."""
    from modules.manhwa.db import get_conn, put_conn

    result = {"stalled_min": None, "undelivered": 0, "alerted": []}
    now = datetime.now(timezone.utc)

    # ── 1. is dispatch RUNNING? ──
    #
    # Liveness comes from cron_run_status (the pipeline's own heartbeat), NOT
    # from dispatch_history.sent_at. Using the ledger was wrong: sending
    # nothing is the normal state when no chapter is new, so "no send for 30
    # min" fired on a perfectly healthy system. The outage this is meant to
    # catch was the worker not running at all, which the heartbeat shows
    # directly.
    last_run = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT MAX(created_at) AS last FROM cron_run_status")
        row = cur.fetchone()
        last_run = (row or {}).get("last") if row else None
        put_conn(conn)
    except Exception as e:
        logger.warn("starvation check: heartbeat read failed", err=str(e)[:160])
        return result

    if last_run is None:
        # No heartbeat at all is a fresh install, not an outage.
        logger.debug("starvation check: no heartbeat yet, skipping")
        return result

    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    stalled_min = (now - last_run).total_seconds() / 60
    result["stalled_min"] = round(stalled_min, 1)

    if stalled_min >= STARVE_AFTER_MIN:
        if _should_alert("starved"):
            _send(
                f"🔴 **Pipeline STOPPED** — no cron run for {stalled_min:.0f} min "
                f"(threshold {STARVE_AFTER_MIN}). Sources may still report healthy. "
                f"Check the cron worker and the queue."
            )
            logger.error("pipeline stalled", stalled_min=round(stalled_min, 1))
            result["alerted"].append("starved")
    else:
        _clear_alert_state("starved")

    # ── 2. eligible but undelivered ──
    # Same definition the dispatcher uses: a row whose (title_key, source) is
    # whitelisted, inside the freshness window, with no FCFS ledger entry.
    undelivered = 0
    oldest_min = 0.0
    try:
        from modules.manhwa.services.fcfs import fcfs_key_for_item

        conn = get_conn()
        cur = conn.cursor()
        cutoff = (now - timedelta(hours=24)).isoformat()
        cur.execute(
            f"""
            SELECT rc.title, rc.title_key, rc.chapter, rc.source, rc.release_date
            FROM recent_chapters rc
            JOIN whitelist w
              ON {RC_WL_JOIN_ON}
            WHERE rc.release_date > %s
            """,
            (cutoff,),
        )
        rows = cur.fetchall() or []

        cur.execute("SELECT fcfs_key FROM dispatch_history WHERE fcfs_key IS NOT NULL")
        sent = {r["fcfs_key"] for r in (cur.fetchall() or []) if r.get("fcfs_key")}
        put_conn(conn)

        for r in rows:
            # Must match the dispatch write path exactly: dispatch_mod builds
            # fcfs_key from it["title"], and the pipeline passes title=title_key
            # for ikiru items. recent_chapters.title is a display string
            # that can differ, so compute from title_key (spaces) instead.
            key = fcfs_key_for_item(r)
            if key in sent:
                continue
            # Skip chapters the dispatch sanity guard refuses. They are
            # whitelisted, inside the window, and have no ledger entry — exactly
            # this check's definition of "undelivered" — but dispatch will never
            # send them, so alerting on them pages the operator forever. Live:
            # shinigami serves chapter_number '26596' for a series whose real
            # chapters are 1..266. Rejected under BOTH batch shapes means no
            # future pass can deliver it; anything the guard could still send
            # stays reported.
            try:
                from modules.manhwa.cron.dispatch_mod import (
                    is_implausible_chapter,
                    load_chapter_ceilings,
                )

                _tk = str(r.get("title_key") or "").strip()
                _src = str(r.get("source") or "").strip()
                _ceil = load_chapter_ceilings([(_tk, _src)]).get((_tk, _src))
                if is_implausible_chapter(r.get("chapter"), _ceil, 1) and is_implausible_chapter(
                    r.get("chapter"), _ceil, 3
                ):
                    continue
            except Exception:
                pass  # fail open: over-reporting beats hiding a real loss
            rd = r.get("release_date")
            if rd is None:
                continue
            if rd.tzinfo is None:
                rd = rd.replace(tzinfo=timezone.utc)
            age_min = (now - rd).total_seconds() / 60
            if age_min >= UNDELIVERED_AFTER_MIN:
                undelivered += 1
                oldest_min = max(oldest_min, age_min)
    except Exception as e:
        logger.warn("starvation check: eligible query failed", err=str(e)[:160])

    result["undelivered"] = undelivered

    if undelivered > 0:
        if ALERT_UNDELIVERED and _should_alert("undelivered"):
            _send(
                f"⚠️ **{undelivered} chapter(s) eligible but UNDELIVERED** — "
                f"oldest {oldest_min:.0f} min. They are whitelisted and inside the "
                f"window with no dispatch record. Check the dispatch claim path."
            )
            logger.error("undelivered chapters", count=undelivered, oldest_min=round(oldest_min, 1))
            result["alerted"].append("undelivered")
        else:
            # The count is already carried by the "dispatch watchdog ok" info
            # line below. A single chapter that is merely late (the rotating
            # walk can legitimately take a while, and a source renumbering
            # shows up here first) self-resolves within a pass or two — warn
            # only when it is a real backlog: several chapters, or one that
            # has been stuck well past the pickup window.
            _is_backlog = undelivered >= 3 or oldest_min >= 180
            if _is_backlog:
                logger.warn(
                    "undelivered chapters (alert suppressed)",
                    count=undelivered, oldest_min=round(oldest_min, 1),
                )
            else:
                logger.info(
                    "undelivered chapters (transient)",
                    count=undelivered, oldest_min=round(oldest_min, 1),
                )
    else:
        _clear_alert_state("undelivered")

    if not result["alerted"]:
        logger.info(
            "dispatch watchdog ok",
            stalled_min=result["stalled_min"],
            undelivered=undelivered,
        )
    return result
