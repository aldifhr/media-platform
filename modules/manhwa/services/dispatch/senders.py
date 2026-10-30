"""The Discord send path: per-item guards, embed build, delivery, ledger flush.

Split out of runner.py. Shares `_RunState` + outcome constants via state.py.
"""
from __future__ import annotations

import html
import time

from modules.manhwa.discord import client as discord
from modules.manhwa.discord.client import _TimeoutSentinel
from modules.manhwa.discord.embeds import add_whitelist_button, build_chapter_embed
from modules.manhwa.logger import get_logger
from modules.manhwa.core.text import deslugify_title_key
from modules.manhwa.services.dispatch.channels import (
    _DEAD_CHANNEL_MAX_FAILS,
    _DEAD_CHANNEL_TTL,
    _DEAD_CHANNELS,
)
from modules.manhwa.services.dispatch.guards import is_implausible_chapter
from modules.manhwa.services.dispatch.state import (
    _BLOCKED,
    _DRY,
    _RunState,
    _SENT,
    _SKIPPED,
)
from modules.manhwa.services.fcfs import fcfs_key, fcfs_key_for_item
from modules.manhwa.storage import dispatch as dispatch_store

logger = get_logger("cron:dispatch")


def _cover_for(run: _RunState, it: dict) -> str:
    cover = str(it.get("cover") or "").strip()
    if not cover:
        tk = str(it.get("title_key") or "").strip()
        src = str(it.get("source") or "").strip()
        cover = run.wl_cover_map.get((tk, src), "") or run.wl_cover_map.get((tk, ""), "")
    return cover


def _flush_sent_claim(run: _RunState, it: dict, url: str) -> None:
    """Write dispatch_history + advance the whitelist marker for one send."""
    try:
        norm = fcfs_key_for_item(it)
        dispatch_store.complete_dispatch_claim(
            chapter_url=url,
            duplicate_url=None,
            instance_id=run.instance_id,
            title_key=it.get("title_key", ""),
            source=it.get("source", ""),
            fcfs_key=norm,
            chapter_title=it.get("chapter", ""),
            cover=_cover_for(run, it),
            series_url=it.get("series_url", "") or "",
        )
        from modules.manhwa.services.dispatch.service import dispatch_service

        cn = float(it.get("chapter", 0) or 0)
        dispatch_service.update_latest_sent_chapter(
            it.get("title_key", ""), it.get("source", ""), cn
        )
    except Exception as e:
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            logger.info("dispatch: history duplicate skip (race)", url=url[:60])
        else:
            logger.warn("dispatch: per-item flush failed", url=url[:60], err=str(e)[:160])

    try:
        from modules.manhwa.metrics_prometheus import track_dispatch

        track_dispatch(source=it.get("source", "unknown"), status="sent", duration=0.8)
    except Exception:
        pass


def _flush_timeout_claim(run: _RunState, it: dict, url: str) -> None:
    """Park a timeout-sentinel send so the next run's FCFS filter skips it."""
    try:
        norm = fcfs_key_for_item(it)
        dispatch_store.complete_dispatch_claim(
            chapter_url=url,
            duplicate_url=None,
            instance_id=run.instance_id,
            title_key=it.get("title_key", ""),
            source=it.get("source", ""),
            fcfs_key=norm,
            chapter_title=it.get("chapter", ""),
            cover=str(it.get("cover") or "").strip(),
            series_url=it.get("series_url", "") or "",
        )
    except Exception as e:
        logger.warn("dispatch: timeout-sentinel history flush failed", err=str(e)[:120])


def _passes_channel_filters(it: dict, type_filter: set[str]) -> bool:
    """Per-guild type filter."""

    if type_filter:
        item_type = str(it.get("type") or "").strip().lower()
        if not item_type or item_type not in type_filter:
            return False
    return True


def _passes_ceiling(run: _RunState, it: dict, tk: str, src: str) -> bool:
    """False when this chapter is at/below a ceiling that was already delivered."""
    ceiling = None if run.skip_ceiling else run.ceilings.get((tk, src))
    if ceiling is None:
        return True
    try:
        chapter = float(it.get("chapter") or "0")
    except (ValueError, TypeError):
        return True
    if chapter > ceiling:
        return True
    # Ceiling blocks re-sends, not first-sends. If this chapter was never
    # delivered (no dispatch_history row), let it through — otherwise a high
    # chapter shipped earlier permanently blocks every lower chapter that was
    # never sent.
    fk = fcfs_key(deslugify_title_key(tk), it.get("chapter"))
    return fk not in run.sent_keys_global


def _send_item(
    it: dict,
    channel: str,
    _is_raw_ch: bool,
    run: _RunState,
    seen_key_run: set[str],
    channel_failures: dict[str, int],
    type_filter: set[str],
) -> str:
    """Run one item through guards, build the embed and send it."""
    url = it.get("url", "")
    if not url or not run.acq_map.get(url):
        return _SKIPPED
    if not _passes_channel_filters(it, type_filter):
        return _SKIPPED

    tk = str(it.get("title_key") or "").strip()
    src = str(it.get("source") or "").strip()
    ceiling = None if run.skip_ceiling else run.ceilings.get((tk, src))
    if not _passes_ceiling(run, it, tk, src):
        return _SKIPPED

    # Sanity: a chapter number far above everything the series has shipped is a
    # bad label, not a release. See is_implausible_chapter for the live 26596
    # case and why a renumber burst still passes.
    if not run.skip_ceiling and is_implausible_chapter(
        it.get("chapter"), ceiling, run.counts.get(tk, 0)
    ):
        # debug, not warn: the upstream keeps serving this label, so the row is
        # re-scraped every cycle and a warn would repeat forever for a condition
        # that is already handled.
        logger.debug(
            "dispatch: implausible chapter number, skipped",
            title_key=tk,
            source=src,
            chapter=str(it.get("chapter")),
            ceiling=ceiling,
        )
        return _SKIPPED

    norm = fcfs_key_for_item(it)
    if norm in run.claimed_keys or norm in seen_key_run or url in run.claimed_urls:
        return _SKIPPED
    # legacy title_key#chapter fallback
    tk_legacy = str(it.get("title_key") or "")
    ch_legacy = str(it.get("chapter") or "")
    try:
        ch_norm = ("%.10g" % float(ch_legacy)) if ch_legacy else ch_legacy
    except (ValueError, TypeError):
        ch_norm = ch_legacy
    if (tk_legacy, ch_legacy) in run.legacy_pairs or (
        tk_legacy,
        ch_norm,
    ) in run.legacy_pairs:
        return _SKIPPED
    seen_key_run.add(norm)

    # Merge static fields from series_meta (canonical) — rss_service does sm > it
    sm = run.sm_map.get((tk, src), {})
    cover = _cover_for(run, it)
    rating = (
        it.get("rating") if it.get("rating") not in (None, "", 0, "0", 0.0) else sm.get("rating")
    )
    description = it.get("description") or sm.get("description") or ""
    genres = it.get("genres") or sm.get("genres") or []

    # NOTE: We intentionally do NOT fetch the cover as a file attachment. The
    # gateway fallback path (used because this VPS IP is banned at Discord's
    # REST API) cannot upload files, and an embed authored with
    # `attachment://` thumbnails fails to render there. Instead we let
    # build_chapter_embed set a real thumbnail URL (now served by the public
    # /api/reader/cover-img proxy), which renders in BOTH the REST and gateway
    # paths.
    embed = build_chapter_embed(
        title=it.get("title", ""),
        chapter=it.get("chapter", ""),
        url=it.get("url", ""),
        series_url=it.get("series_url", ""),
        source=it.get("source", ""),
        cover=cover,
        rating=str(rating if rating not in (None, "") else ""),
        genres=genres,
        description=description,
        updated_time=it.get("updated_time", ""),
    )
    content = (
        f"🔔 New Release on **{html.unescape(it.get('title', 'Unknown'))}** "
        f"— Chapter {it.get('chapter', '?')}"
    )
    # Raw feed only: the button is what makes a raw announcement actionable. A
    # whitelisted series gets the same notification on the whitelist feed, where
    # the button would be a no-op.
    components = None
    if _is_raw_ch:
        components = (
            add_whitelist_button(
                source=str(it.get("source") or ""),
                title_key=str(it.get("title_key") or ""),
                title=str(it.get("title") or ""),
                series_url=str(it.get("series_url") or ""),
            )
            or None
        )

    if run.dry_run:
        return _DRY

    try:
        resp = discord.send_channel_message(
            channel_id=channel, content=content, embeds=[embed], components=components
        )
        if resp is None:
            fails = channel_failures.get(channel, 0) + 1
            channel_failures[channel] = fails
            logger.warn(
                "dispatch: send returned None",
                title=(it.get("title") or "")[:40],
                consecutive=fails,
                channel=channel,
            )
            if url not in run.sent_urls:
                dispatch_store.unclaim(url)
            if fails >= _DEAD_CHANNEL_MAX_FAILS:
                try:
                    dispatch_store.record_failed(
                        chapter_url=url,
                        title_key=it.get("title_key", ""),
                        source=it.get("source", ""),
                        chapter_title=str(it.get("chapter", "")),
                        chapter_number=None,
                        error_message="gateway burst threshold (3 consecutive None)",
                        error_code="DISCORD_GATEWAY_BURST",
                    )
                except Exception:
                    pass
                # Block this channel for the TTL: a channel that 404s fails
                # every run, and the retry pass then re-sends the chapter to a
                # live channel — a duplicate notification.
                _DEAD_CHANNELS[channel] = time.monotonic()
                logger.error(
                    "dispatch: burst threshold hit, blocking channel",
                    channel=channel,
                    ttl_s=_DEAD_CHANNEL_TTL,
                )
                time.sleep(0.8)
                return _BLOCKED
            time.sleep(0.8)
            return _SKIPPED
        # Timeout sentinel: unknown result. Do NOT unclaim (message may have
        # landed). Park it in dispatch_history so the next run FCFS filter skips
        # it and no duplicate is sent.
        if isinstance(resp, _TimeoutSentinel):
            run.sent_urls.add(url)
            _flush_timeout_claim(run, it, url)
            time.sleep(0.8)
            return _SENT
        channel_failures[channel] = 0
        run.sent_urls.add(url)
        # Flush dispatch_history + whitelist marker immediately so a later crash
        # does not re-send chapters that already landed.
        _flush_sent_claim(run, it, url)
        time.sleep(0.8)
        return _SENT
    except Exception as derr:
        fails = channel_failures.get(channel, 0) + 1
        channel_failures[channel] = fails
        try:
            from modules.manhwa.metrics_prometheus import track_dispatch_error

            track_dispatch_error(error_type=type(derr).__name__)
        except Exception:
            pass
        dispatch_store.record_failed(
            chapter_url=url,
            title_key=it.get("title_key", ""),
            source=it.get("source", ""),
            chapter_title=str(it.get("chapter", "")),
            chapter_number=None,
            error_message=str(derr)[:500],
            error_code="DISCORD_SEND_FAILED",
        )
        dispatch_store.unclaim(url)
        if fails >= _DEAD_CHANNEL_MAX_FAILS:
            _DEAD_CHANNELS[channel] = time.monotonic()
            logger.error(
                "dispatch: burst threshold hit (exception), blocking channel",
                channel=channel,
                consecutive=fails,
                ttl_s=_DEAD_CHANNEL_TTL,
            )
            time.sleep(0.8)
            return _BLOCKED
        time.sleep(0.8)
        return _SKIPPED


def _flush_history(run: _RunState, to_send: list[dict]) -> None:
    """Post-loop safety net: re-flush everything sent this pass."""
    if not run.sent_urls or run.dry_run:
        return
    try:
        by_url = {it.get("url", ""): it for it in to_send if it.get("url")}
        for url in run.sent_urls:
            it = by_url.get(url)
            if not it:
                continue
            # Build the fcfs key from title_key via fcfs_key_for_item, the same
            # helper the watchdog (dispatch_watchdog.py) uses, so both sides
            # agree on the key. normalize_title() now delegates to
            # slugify_title_key, which canonicalises a display title and its
            # title_key to the same slug — the older warning here claimed the
            # two forms produced different keys, which described a
            # normalize_title that no longer exists.
            norm = fcfs_key_for_item(it)
            try:
                dispatch_store.complete_dispatch_claim(
                    chapter_url=url,
                    duplicate_url=None,
                    instance_id=run.instance_id,
                    title_key=it.get("title_key", ""),
                    source=it.get("source", ""),
                    fcfs_key=norm,
                    chapter_title=it.get("chapter", ""),
                    cover=_cover_for(run, it),
                    series_url=it.get("series_url", "") or "",
                )
            except Exception as e:
                msg = str(e).lower()
                if "dispatch_history_uq" in msg or "unique" in msg or "duplicate" in msg:
                    logger.info(
                        "dispatch: history duplicate skip (race)",
                        url=url[:60],
                        err=str(e)[:120],
                    )
                    continue
                logger.warn("dispatch: complete_claim failed", url=url[:60], err=str(e)[:160])
                continue
            # Update whitelist marker so gap detector doesn't false-positive
            try:
                from modules.manhwa.services.dispatch.service import dispatch_service

                cn = float(it.get("chapter", 0) or 0)
                dispatch_service.update_latest_sent_chapter(
                    it.get("title_key", ""), it.get("source", ""), cn
                )
            except Exception:
                pass
    except Exception as e:
        logger.warn("dispatch_history flush failed", err=str(e)[:160])
