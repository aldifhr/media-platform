"""The pahe check, in one place.

Both entrypoints used to carry their own copy of this flow — scrape, filter,
notify, mark seen — and the copies had drifted apart (different freshness
rules, different lock behaviour). ``media.py``'s copy additionally "imported"
the in-process lock with ``from ... import _checking`` and then rebound that
name locally, so the guard it relied on never actually engaged and two
concurrent checks could both scrape and both notify.

This module owns the flow. The routers are thin.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from modules.media.common.env import env_int, env_list, env_str, load_env
from modules.media.common.log import get_logger
from modules.media.sources.pahe.scanner import scraper
from modules.media.sources.pahe.scanner.notify import send_bulk_notifications
from modules.media.sources.pahe.scanner.rules import is_new_post, is_recheck_candidate
from modules.media.common.state import State
import modules.media.source_health as sh

load_env()

log = get_logger("pahe")

SEEN = State("pahe")
CHECK_TIMEOUT_MS = env_int("PAHE_CHECK_TIMEOUT_MS", 60_000)
ALLOWED_TAGS = env_list("PAHE_ALLOWED_TAGS", pattern=r"[A-Za-z0-9_-]+")

# One lock per running event loop. A module-level asyncio.Lock binds itself to
# the first loop that awaits it and then raises if a different loop touches it,
# which breaks any test that calls asyncio.run() more than once.
_locks: dict[int, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    key = id(asyncio.get_running_loop())
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def min_year() -> int:
    """Movie release-year gate. 0 disables it; serials ignore it either way.

    A non-numeric value is a typo in ``.env``, and the old behaviour was to
    fall back to 0 — which disables the gate entirely and notifies every film
    pahe has ever back-filled. Log it loudly instead of silently changing what
    gets sent.
    """
    raw = env_str("PAHE_MIN_YEAR")
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        log.error(
            "PAHE_MIN_YEAR=%r is not a number; treating as 0 (notify everything). "
            "Fix .env — this is almost certainly a typo.",
            raw,
        )
        return 0


def token_configured() -> bool:
    return bool(env_str("PAHE_API_TOKEN"))


def _result(status: int, body: dict[str, Any], started: float) -> dict[str, Any]:
    return {
        "source": "pahe",
        "status": status,
        "body": json.dumps(body),
        "rt_ms": int((time.time() - started) * 1000),
    }


async def run_check(
    *, dry_run: bool = False, seed: bool = False, recheck: bool = False
) -> dict[str, Any]:
    """Scrape pahe, notify new/updated posts, and record the outcome.

    ``dry_run`` scrapes and reports what *would* be sent without touching
    seen-state or Discord. ``seed`` backfills seen-state without notifying.
    ``recheck`` re-examines already-seen posts for changed download links.

    Returns ``{"source", "status", "body", "rt_ms"}`` — the same shape the old
    HTTP call to port 3100 produced, so callers are unchanged.
    """
    started = time.time()

    if not token_configured():
        sh.record("pahe", ok=False)
        return _result(500, {"ok": False, "error": "PAHE_API_TOKEN not set"}, started)

    skip, why = sh.should_skip("pahe")
    if skip:
        return _result(200, {"ok": True, "skipped": True, "reason": why}, started)

    if _lock().locked():
        return _result(429, {"ok": False, "error": "Check already in progress"}, started)

    async with _lock():
        return await _run_locked(dry_run=dry_run, seed=seed, recheck=recheck, started=started)


def _needs_details(gate: int, recheck: bool):
    """Which posts are worth an HTTP round-trip for their page.

    New posts always are. Under ``recheck`` a seen post is too — otherwise its
    ``downloads`` stay empty, ``content_hash`` stays ``""``, and the change
    detection can never fire, which made ``recheck`` dead code in practice.
    """

    def predicate(post) -> bool:
        if is_new_post(post, SEEN, gate):
            return True
        return recheck and is_recheck_candidate(post, SEEN, gate)

    return predicate


async def _run_locked(
    *, dry_run: bool, seed: bool, recheck: bool, started: float
) -> dict[str, Any]:
    gate = min_year()
    try:
        posts = await asyncio.wait_for(
            scraper.scrape_latest_posts(ALLOWED_TAGS, needs_details=_needs_details(gate, recheck)),
            timeout=CHECK_TIMEOUT_MS / 1000,
        )
    except scraper.FeedUnavailable as exc:
        # Every feed failed. This is NOT "no new posts" — record a failure so
        # the quarantine can arm, instead of reporting a green check.
        sh.record("pahe", ok=False, error=str(exc))
        log.error("check aborted: %s", exc)
        return _result(503, {"ok": False, "error": "Feed unavailable"}, started)
    except TimeoutError:
        sh.record("pahe", ok=False)
        log.warning("check timed out after %sms", CHECK_TIMEOUT_MS)
        return _result(504, {"ok": False, "error": "Check timed out"}, started)
    except Exception as exc:
        sh.record("pahe", ok=False)
        log.exception("check failed: %s", exc)
        return _result(500, {"ok": False, "error": "Internal error"}, started)

    new_posts = [p for p in posts if is_new_post(p, SEEN, gate)]

    if seed:
        # Backfill: mark everything seen, notify nobody.
        SEEN.mark_seen_batch([p.id for p in posts])
        sh.record("pahe", ok=True)
        return _result(
            200,
            {
                "ok": True,
                "seeded": len(posts),
                "message": "Seeded seen-state, no notifications sent",
            },
            started,
        )

    if dry_run:
        # Report only. Seen-state is deliberately untouched: the old code
        # marked every post seen here, so a single dry run muted the source
        # until the state was pruned.
        sh.record("pahe", ok=True)
        return _result(
            200,
            {
                "ok": True,
                "dry": True,
                "checked": len(posts),
                "new": len(new_posts),
                "posts": [p.title for p in new_posts],
            },
            started,
        )

    updated_posts: list = []
    if recheck:
        for post in posts:
            if not is_recheck_candidate(post, SEEN, gate):
                continue
            record = SEEN.get(post.id) or {}
            previous = record.get("hash") if isinstance(record, dict) else None
            if previous and previous != scraper.content_hash(post):
                updated_posts.append(post)

    to_notify = [*new_posts, *updated_posts]
    if not to_notify:
        sh.record("pahe", ok=True)
        return _result(
            200,
            {
                "ok": True,
                "checked": len(posts),
                "new": 0,
                "updated": 0,
                "notified": 0,
                "message": "No new or updated posts",
            },
            started,
        )

    # Delivery is defensive on its own, but it sits outside the scrape guard
    # above — an unexpected raise here used to escape run_check entirely and
    # surface as an unhandled 500 from the endpoint.
    try:
        result = await send_bulk_notifications(to_notify)
    except Exception as exc:
        sh.record("pahe", ok=False)
        log.exception("notification batch failed: %s", exc)
        return _result(500, {"ok": False, "error": "Notification failed"}, started)

    successful = {item["id"] for item in result["results"] if item["ok"]}
    for post in to_notify:
        if post.id in successful:
            SEEN.mark_seen(post.id, value={"title": post.title, "hash": scraper.content_hash(post)})
    SEEN.prune(days=2)
    sh.record("pahe", ok=result["failed"] == 0)

    return _result(
        200,
        {
            "ok": True,
            "checked": len(posts),
            "new": len(new_posts),
            "updated": len(updated_posts),
            "notified": result["success"],
            "failed": result["failed"],
            "posts": [p.title for p in to_notify],
        },
        started,
    )
