"""Dispatch retry — auto-retry failed Discord sends.

Exponential backoff, unlimited retries (until chapter pruned from 24h window).
Separates transient failures (retry) from permanent (give up).

Strategy:
- Backoff: 5min → 10min → 20min → 40min → 60min (cap)
- Max retries: unlimited, BUT chapter must still be in recent_chapters (24h window)
- If Discord circuit breaker is open: skip retry (wastes attempts)
- Priority: newest failures first
- Permanent failure detection: Discord 4xx = don't retry (bad URL, deleted, etc.)
"""

from __future__ import annotations

from datetime import UTC, datetime

from modules.manhwa.core.resilience import cb_discord
from modules.manhwa.logger import get_logger
from modules.manhwa.storage.dispatch import complete_dispatch_claim

logger = get_logger("services:dispatch_retry")

# Exponential backoff: delay = min(base * 2^attempt, cap)
RETRY_BASE_S = 300  # 5 min for first retry
RETRY_CAP_S = 3600  # 1 hour max
RETRY_PERMANENT_CODES = {400, 401, 403, 404, 405, 410, 422}  # Discord: don't retry

# Legacy constants (kept for backward compat — dispatch.py imports these)
MAX_RETRY_ATTEMPTS = 999  # effectively unlimited (window-based)
RETRY_COOLDOWN_S = RETRY_BASE_S


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with cap."""
    delay = RETRY_BASE_S * (2**attempt)
    return min(delay, RETRY_CAP_S)


def retry_failed_dispatches(channel_ids: list[str] | None = None) -> dict:
    """Attempt to re-send chapters parked in failed_dispatches.

    For each row with status='failed':
    - Skip if Discord circuit breaker is open
    - Skip if backoff delay hasn't elapsed
    - Skip if chapter no longer in recent_chapters (24h window)
    - Skip if error_code indicates permanent failure
    - Rebuild embed from remaining metadata + send to Discord

    On success: mark 'resolved', record in dispatch_history.
    On failure: increment retry_count, update updated_at (triggers backoff).

    Returns stats dict {retried, resent, skipped_cb, skipped_window,
    skipped_permanent, still_failed}.
    """
    from modules.manhwa.cron.dispatch_mod import _load_channels, fcfs_key
    from modules.manhwa.db import get_supabase
    from modules.manhwa.discord import client as discord
    from modules.manhwa.discord.client import _TimeoutSentinel
    from modules.manhwa.discord.embeds import build_chapter_embed

    now = datetime.now(UTC)

    # Skip if Discord is down — retrying wastes attempts and burns backoff
    if cb_discord.is_open():
        logger.info("retry_failed: Discord circuit OPEN, skipping retry pass")
        return {
            "retried": 0,
            "resent": 0,
            "skipped_cb": 0,
            "skipped_window": 0,
            "skipped_permanent": 0,
            "skipped_unsubscribed": 0,
            "unknown": 0,
            "still_failed": 0,
        }

    # Recover rows a crashed pass left in 'retrying'. The claim below only
    # looks at status='failed', so without this reaper a row claimed by a pass
    # that then died (whitelist read error, no channels, exception) is never
    # selected again and stays 'retrying' forever. 10 min >> a normal pass.
    try:
        from datetime import timedelta as _td

        _stale_cutoff = (now - _td(minutes=10)).isoformat()
        (
            get_supabase()
            .table("failed_dispatches")
            .update({"status": "failed"})
            .eq("status", "retrying")
            .lt("updated_at", _stale_cutoff)
            .execute()
        )
    except Exception as e:
        # This reaper is the only thing that recovers rows a crashed pass left
        # in 'retrying'. Swallowing it silently meant a pass that died holding
        # rows parked them forever with no signal anywhere — the rows simply
        # stopped being retried. Log it so the failure is visible.
        logger.error("retry_failed: stale 'retrying' reaper failed", err=str(e)[:160])

    try:
        rows = (
            get_supabase()
            .table("failed_dispatches")
            .select("*")
            .eq("status", "failed")
            .order("updated_at", desc=False)  # oldest first (most overdue)
            .limit(50)
            .execute()
        ).data or []
    except Exception as e:
        logger.error("retry_failed: load failed", exc=e)
        return {
            "retried": 0,
            "resent": 0,
            "skipped_cb": 0,
            "skipped_window": 0,
            "skipped_permanent": 0,
            "skipped_unsubscribed": 0,
            "unknown": 0,
            "still_failed": 0,
        }

    if not rows:
        return {
            "retried": 0,
            "resent": 0,
            "skipped_cb": 0,
            "skipped_window": 0,
            "skipped_permanent": 0,
            "skipped_unsubscribed": 0,
            "unknown": 0,
            "still_failed": 0,
        }

    # Atomically claim these rows so a concurrent retry pass (or the API
    # retry-all endpoint) cannot send the same chapters twice.
    _urls = [str(r.get("chapter_url", "")) for r in rows if r.get("chapter_url")]
    _claimed: set[str] = set()
    if _urls:
        try:
            from modules.manhwa.db_adapter import get_conn, put_conn

            conn = get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    # Claim WITHOUT resetting updated_at. Resetting it made the
                    # backoff check below see elapsed≈0 and skip every row, so
                    # the rows stayed 'retrying' and were never retried.
                    "UPDATE failed_dispatches SET status='retrying' "
                    "WHERE status='failed' AND chapter_url=ANY(%s) RETURNING chapter_url",
                    (_urls,),
                )
                _claimed = {str(r["chapter_url"]) for r in cur.fetchall()}
            finally:
                put_conn(conn)
        except Exception as e:
            logger.error("retry_failed: atomic claim failed", err=str(e)[:160])

    rows = [r for r in rows if str(r.get("chapter_url", "")) in _claimed]

    channels = channel_ids or _load_channels()
    if not channels:
        return {
            "retried": 0,
            "resent": 0,
            "skipped_cb": 0,
            "skipped_window": 0,
            "skipped_permanent": 0,
            "skipped_unsubscribed": 0,
            "unknown": 0,
            "still_failed": len(rows),
        }

    # Route each retry to the same feed dispatch() would use. Without this the
    # retry pass sent to EVERY channel, so a whitelisted series whose first
    # send failed was re-delivered on the raw channel — a whitelisted chapter
    # showing up in RAW, with the raw feed's own copy nowhere.
    from modules.manhwa.cron.dispatch_mod import split_dispatch_targets

    _raw_ch, _wl_ch = split_dispatch_targets(channels)

    # Build URL→row mapping
    url_to_row: dict[str, dict] = {}
    for r in rows:
        url = str(r.get("chapter_url", ""))
        if url:
            url_to_row[url] = r

    # Filter: check if chapter still exists in recent_chapters (24h window)
    urls = list(url_to_row.keys())
    rc_by_url: dict[str, dict] = {}
    if urls:
        try:
            rc = (
                get_supabase()
                .table("recent_chapters")
                .select(
                    "chapter_url, title_key, title, chapter, cover, series_url, "
                    "source, origin, description, updated_time, rating, genres"
                )
                .in_("chapter_url", urls)
                .execute()
            )
            for r in rc.data or []:
                if r.get("chapter_url"):
                    rc_by_url[str(r["chapter_url"])] = r
        except Exception as e:
            logger.error("retry_failed: rc lookup failed", exc=e)

    retried = 0
    resent = 0
    skipped_cb = 0
    skipped_window = 0
    skipped_permanent = 0
    still_failed = 0
    # Sends whose outcome was unknown (timeout sentinel). Booked as delivered,
    # counted separately so the operator can see how much of `resent` is a
    # guess rather than a confirmed post.
    unknown_count = 0

    # Whitelist gate. failed_dispatches rows can outlive the whitelist entry
    # that produced them (the user unsubscribes while a send is parked), and
    # this path previously re-sent whatever was parked — so a removed series
    # kept notifying. Retry re-delivers a FAILED send; it must not resurrect a
    # subscription the user cancelled.
    #
    # Source-strict, matching filter_whitelisted() in cron/collect.py: the
    # whitelist has one row per (title_key, source), so a series subscribed on
    # shinigami is NOT subscribed on another source. Matching on the title alone sent
    # notifications from sources nobody subscribed to.
    from modules.manhwa.core.text import slugify_title_key as _slugify

    try:
        wl_rows = get_supabase().table("whitelist").select("title_key, source").execute().data or []
        wl_pairs = {
            (_slugify(str(w.get("title_key") or "")), str(w.get("source") or "").lower())
            for w in wl_rows
        }
        wl_pairs.discard(("", ""))
    except Exception as e:
        # Fail CLOSED: without the whitelist we cannot tell a live subscription
        # from a cancelled one, and sending to a cancelled one is the bug.
        logger.error("retry_failed: whitelist load failed, skipping pass", exc=e)
        return {
            "retried": 0,
            "resent": 0,
            "skipped_cb": 0,
            "skipped_window": 0,
            "skipped_permanent": 0,
            "still_failed": len(rows),
        }

    # Static series metadata (cover/rating/genres/description/type) lives in
    # series_meta, NOT recent_chapters. A retry built only from recent_chapters
    # produced an embed with no description, no genres and "Released: Unknown" —
    # the exact degraded duplicate the operator reported.
    sm_by_key: dict[tuple[str, str], dict] = {}
    try:
        from modules.manhwa.storage.series_meta import load_series_meta_map

        _tks = [
            str(r.get("title_key") or "").strip() for r in rc_by_url.values() if r.get("title_key")
        ]
        sm_by_key = load_series_meta_map(get_supabase(), _tks)
    except Exception as e:
        logger.warning("retry_failed: series_meta lookup failed", err=str(e)[:120])

    # Already-notified guard. dispatch_history is the permanent record of a
    # delivered message. A failed row left by a dead channel (Discord 404) sits
    # next to a successful send on the live channel; retrying it posts the
    # chapter a second time.
    #
    # Checked on BOTH chapter_url and fcfs_key. chapter_url alone is not
    # enough: sources rotate chapter URLs between scrapes, and two sources can
    # publish the same title+chapter under different URLs. The row parked here
    # carries the OLD url while dispatch_history holds the NEW one, so a
    # url-only lookup misses the delivery and the retry re-posts it. Every
    # other send path in this codebase dedupes on fcfs_key; this one did not.
    already_sent: set[str] = set()
    already_sent_fcfs: set[str] = set()
    try:
        _dh = (
            get_supabase()
            .table("dispatch_history")
            .select("chapter_url, fcfs_key")
            .in_("chapter_url", urls)
            .execute()
            .data
            or []
        )
        for r in _dh:
            u = str(r.get("chapter_url") or "")
            if u:
                already_sent.add(u)
            fk = r.get("fcfs_key")
            if fk:
                already_sent_fcfs.add(str(fk))
    except Exception as e:
        logger.warning("retry_failed: dispatch_history lookup failed", err=str(e)[:120])

    # fcfs_keys already notified for the titles in this batch, fetched
    # independently of chapter_url so a rotated URL cannot hide a delivery.
    if urls:
        try:
            _tks = sorted(
                {
                    str(r.get("title_key") or "").strip()
                    for r in rows
                    if str(r.get("title_key") or "").strip()
                }
            )
            if _tks:
                _fh = (
                    get_supabase()
                    .table("dispatch_history")
                    .select("fcfs_key")
                    .in_("title_key", _tks)
                    .execute()
                    .data
                    or []
                )
                for r in _fh:
                    fk = r.get("fcfs_key")
                    if fk:
                        already_sent_fcfs.add(str(fk))
        except Exception as e:
            logger.warning("retry_failed: dispatch_history fcfs lookup failed", err=str(e)[:120])

    skipped_unsubscribed = 0
    skipped_duplicate = 0

    for row in rows:
        url = str(row.get("chapter_url", ""))
        if not url:
            continue

        # Derived up here rather than at the send site: the dedupe check below
        # needs the same fcfs_key the ledger write will use, and building it
        # twice from two different sources is how the two drift apart.
        meta = rc_by_url.get(url, {})
        title = str(meta.get("title") or row.get("title_key") or "Unknown")
        chapter = str(
            meta.get("chapter") or row.get("chapter_number") or row.get("chapter_title") or "?"
        )
        source = str(meta.get("source") or row.get("source", ""))
        row_fk = fcfs_key(title, chapter)

        if url in already_sent or row_fk in already_sent_fcfs:
            try:
                get_supabase().table("failed_dispatches").update(
                    {
                        "status": "resolved",
                        "updated_at": now.isoformat(),
                        "error_message": "Already notified on a live channel",
                    }
                ).eq("chapter_url", url).execute()
                skipped_duplicate += 1
            except Exception as e:
                logger.warning(
                    "retry: failed_dispatches update failed", url=url[:80], err=str(e)[:120]
                )
            continue

        # Drop rows whose (title, source) is no longer whitelisted (see gate above).
        _row_src = str(row.get("source") or "").lower()
        if (_slugify(str(row.get("title_key") or "")), _row_src) not in wl_pairs:
            try:
                get_supabase().table("failed_dispatches").update(
                    {
                        "status": "resolved",
                        "updated_at": now.isoformat(),
                        "error_message": "Series no longer whitelisted",
                    }
                ).eq("chapter_url", url).execute()
                skipped_unsubscribed += 1
            except Exception as e:
                logger.warning(
                    "retry: failed_dispatches update failed", url=url[:80], err=str(e)[:120]
                )
            continue

        # Check permanent failure
        error_code = str(row.get("error_code", "UNKNOWN"))
        try:
            error_num = int(error_code) if error_code not in ("UNKNOWN", "") else 0
        except ValueError:
            error_num = 0
        if error_num in RETRY_PERMANENT_CODES:
            # Mark as permanently failed — won't retry
            try:
                get_supabase().table("failed_dispatches").update(
                    {"status": "permanent_failure", "updated_at": now.isoformat()}
                ).eq("chapter_url", url).execute()
                skipped_permanent += 1
            except Exception as e:
                logger.warning(
                    "retry: failed_dispatches update failed", url=url[:80], err=str(e)[:120]
                )
            continue

        # Check backoff
        retry_count = int(row.get("retry_count", 0) or 0)
        last_attempt = str(row.get("updated_at", ""))
        if last_attempt:
            try:
                last_dt = datetime.fromisoformat(last_attempt.replace("Z", "+00:00"))
                elapsed = (now - last_dt).total_seconds()
                required_delay = _backoff_delay(retry_count)
                if elapsed < required_delay:
                    continue  # not yet time
            except Exception:
                pass  # if parse fails, just retry

        # Check if chapter still in window
        if not meta:
            # Chapter no longer in recent_chapters — pruned from 24h window
            # Mark resolved (no longer relevant)
            try:
                get_supabase().table("failed_dispatches").update(
                    {
                        "status": "resolved",
                        "updated_at": now.isoformat(),
                        "error_message": "Chapter pruned from 24h window",
                    }
                ).eq("chapter_url", url).execute()
                skipped_window += 1
            except Exception as e:
                logger.warning(
                    "retry: failed_dispatches update failed", url=url[:80], err=str(e)[:120]
                )
            continue

        # Attempt send. title/chapter/source/row_fk were derived above.
        _sm = sm_by_key.get((str(meta.get("title_key") or "").strip(), source.strip()), {})
        _rating = (
            meta.get("rating")
            if meta.get("rating") not in (None, "", 0, "0", 0.0)
            else _sm.get("rating")
        )

        item = {
            "title": title,
            "chapter": chapter,
            "url": url,
            "series_url": str(meta.get("series_url", "")),
            "source": source,
            "cover": str(meta.get("cover") or _sm.get("cover") or ""),
            "status": "",
            "rating": str(_rating if _rating not in (None, "") else ""),
            "genres": meta.get("genres") or _sm.get("genres") or [],
            "description": str(meta.get("description") or _sm.get("description") or ""),
            "updated_time": str(meta.get("updated_time") or ""),
        }

        embed = build_chapter_embed(
            title=item["title"],
            chapter=item["chapter"],
            url=url,
            series_url=item["series_url"],
            source=item["source"],
            cover=item["cover"],
            rating=item["rating"],
            genres=item["genres"],
            description=item["description"],
            updated_time=item["updated_time"],
        )
        content = f"🔔 New Release on **{title}** — Chapter {item['chapter']}"

        sent_ok = False
        unknown = False
        retried += 1
        # Retry rows are whitelist-gated above, so only whitelist channels are
        # valid destinations. Never fall back to raw here.
        for ch in _wl_ch:
            try:
                resp = discord.send_channel_message(channel_id=ch, content=content, embeds=[embed])
                # A timeout sentinel means "unknown" — the message may or may
                # not have landed. `resp is not None` used to be the whole
                # success test here, and a sentinel is not None, so every
                # timed-out retry was booked as delivered: dispatch_history got
                # a row for a chapter nobody received and the
                # failed_dispatches row was marked resolved, so it was never
                # attempted again. senders._send_item has always special-cased
                # the sentinel; this path never did.
                if isinstance(resp, _TimeoutSentinel):
                    unknown = True
                    break
                if resp is not None:
                    sent_ok = True
                    break
            except Exception as e:
                logger.error("retry send to channel failed", channel=ch, exc=e)
                continue

        try:
            if sent_ok or unknown:
                # `unknown` is booked as delivered, matching the timeout path
                # in senders._send_item. A lost notification is recoverable —
                # the chapter is still in recent_chapters and the gap detector
                # or a manual resend can reach it. A duplicate is not: the
                # user already saw it, and re-posting trains them to ignore
                # the feed.
                complete_dispatch_claim(
                    url,
                    None,
                    "retry",
                    str(row.get("title_key", "unknown")),
                    source,
                    fcfs_key=row_fk,
                    chapter_title=chapter,
                )
                get_supabase().table("failed_dispatches").update(
                    {"status": "resolved", "updated_at": now.isoformat()}
                ).eq("chapter_url", url).execute()
                resent += 1
                if unknown:
                    unknown_count += 1
            else:
                get_supabase().table("failed_dispatches").update(
                    {
                        "status": "failed",
                        "retry_count": retry_count + 1,
                        "updated_at": now.isoformat(),
                    }
                ).eq("chapter_url", url).execute()
                still_failed += 1
        except Exception as e:
            logger.error("retry_failed: update row failed", exc=e)
            still_failed += 1

    logger.info(
        f"retry_failed completed: retried={retried} resent={resent} unknown={unknown_count} "
        f"skipped_cb={skipped_cb} skipped_window={skipped_window} "
        f"skipped_permanent={skipped_permanent} skipped_unsubscribed={skipped_unsubscribed} "
        f"skipped_duplicate={skipped_duplicate} still_failed={still_failed}"
    )
    return {
        "retried": retried,
        "resent": resent,
        "skipped_cb": skipped_cb,
        "skipped_window": skipped_window,
        "skipped_permanent": skipped_permanent,
        "skipped_unsubscribed": skipped_unsubscribed,
        "skipped_duplicate": skipped_duplicate,
        "unknown": unknown_count,
        "still_failed": still_failed,
    }
