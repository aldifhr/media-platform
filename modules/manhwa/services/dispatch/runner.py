"""The Discord send pass: dedupe, guards, per-feed delivery, ledger flush.

Extracted from app/cron/dispatch_mod.py. dispatch_mod re-exports `dispatch`
for backward compatibility with existing call sites and tests.
"""

from __future__ import annotations

import re

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger
from modules.manhwa.services.dispatch.backfill import (
    _split_send_backfill,
    backfill_dispatch_history,
)
from modules.manhwa.services.dispatch.guards import load_chapter_ceilings
from modules.manhwa.services.dispatch.senders import (
    _flush_history,
    _send_item,
)
from modules.manhwa.services.dispatch.state import (
    _BLOCKED,
    _DRY,
    _SENT,
    _RunState,
)
from modules.manhwa.services.fcfs import fcfs_key_for_item
from modules.manhwa.storage import dispatch as dispatch_store

logger = get_logger("cron:dispatch")


def _item_url(it: dict) -> str:
    return it.get("url") or it.get("chapter_url")


def _backfill(to_backfill: list[dict], instance_id: str, dry_run: bool) -> None:
    """Record the HTML backlog as already-dispatched without notifying."""
    if not to_backfill or dry_run:
        return
    bf_urls = [_item_url(it) for it in to_backfill if _item_url(it)]
    already = dispatch_store._already_dispatched(bf_urls) if bf_urls else set()
    truly_new = [it for it in to_backfill if _item_url(it) not in already]
    if truly_new:
        bf = backfill_dispatch_history(truly_new, instance_id)
        logger.info(
            "dispatch backfill (silent)",
            count=bf,
            skipped=len(to_backfill) - len(truly_new),
        )


def _load_sent_keys_global(title_keys: list[str] | None = None) -> set[str]:
    """fcfs_keys already in dispatch_history, for the ceiling guard.

    Scoped to `title_keys` when given. This used to be an unbounded
    `SELECT fcfs_key FROM dispatch_history` on every dispatch run — the whole
    permanent ledger, growing forever, loaded into a set to answer a question
    that is only ever asked about the chapters in the current batch. The
    ceiling guard (`_passes_ceiling`) looks up an fcfs_key derived from the
    item's own title_key, so filtering the read by the batch's title_keys
    returns exactly the keys that can be asked about.

    A read failure returns an empty set, which makes the ceiling guard treat
    every below-ceiling chapter as "never sent" and let it through. That is
    survivable only because `claimed_keys` (the real FCFS dedupe, loaded
    separately in `dispatch`) still filters it — but it is still a silent
    degradation, so it is logged rather than swallowed.
    """
    out: set[str] = set()
    try:
        from modules.manhwa.db import get_supabase

        q = get_supabase().table("dispatch_history").select("fcfs_key")
        keys = [str(t or "").strip() for t in (title_keys or []) if str(t or "").strip()]
        if keys:
            q = q.in_("title_key", sorted(set(keys)))
        rows = q.execute().data or []
        for r in rows:
            fk = r.get("fcfs_key")
            if fk:
                out.add(str(fk))
    except Exception as e:
        logger.warn(
            "dispatch: sent_keys_global load failed — ceiling guard degraded",
            err=str(e)[:160],
        )
    return out


def _load_claimed_keys(all_keys: list[str]) -> set[str]:
    """fcfs_keys from dispatch_history — the permanent "already notified" set."""
    from modules.manhwa.storage.dispatch.history import _already_dispatched_fcfs

    return _already_dispatched_fcfs(list({k for k in all_keys if k}))


def _load_legacy_pairs(to_send: list[dict]) -> set[tuple[str, str]]:
    """(title_key, chapter_title) pairs from history, plus normalized numbers."""
    pairs: set[tuple[str, str]] = set()
    try:
        from modules.manhwa.db import get_supabase

        tks = list({str(it.get("title_key") or "") for it in to_send if it.get("title_key")})
        if not tks:
            return pairs
        rows = (
            get_supabase()
            .table("dispatch_history")
            .select("title_key,chapter_title")
            .in_("title_key", tks)
            .execute()
            .data
            or []
        )
        for r in rows:
            tkh = str(r.get("title_key") or "")
            cth = str(r.get("chapter_title") or "")
            if tkh and cth:
                pairs.add((tkh, cth))
                try:
                    # %.10g kept verbatim: switching to a format specifier is
                    # an unsafe fix that can change the normalized chapter key.
                    pairs.add((tkh, "%.10g" % float(cth)))  # noqa: UP031
                except (ValueError, TypeError):
                    pass
    except Exception:
        return set()
    return pairs


def _filter_junk_urls(to_send: list[dict]) -> list[dict]:
    """Drop URLs that do not match a known source's public base."""
    ikiru_pub = (
        getattr(settings, "IKIRU_PUBLIC_BASE", "https://09.ikiru.wtf") or "https://09.ikiru.wtf"
    )
    valid_prefixes = (
        f"{settings.SHINIGAMI_PUBLIC_BASE}{settings.SHINIGAMI_CHAPTER_PATH}",
        ikiru_pub,
    )
    junk = {
        u
        for u in [it.get("url", "") for it in to_send if it.get("url")]
        if not any(u.startswith(p) for p in valid_prefixes)
    }
    if not junk:
        return to_send
    logger.debug("dispatch: filtering junk urls", count=len(junk), examples=list(junk)[:3])
    return [it for it in to_send if it.get("url") not in junk]


def _build_series_meta_map(
    to_send: list[dict],
) -> tuple[dict[tuple[str, str], dict], dict[tuple[str, str], str]]:
    """title_key+source -> series_meta row / cover map.

    rss_service reads sm_map > it > wl; dispatch used to read it.cover only, so
    ratings rendered as 0/No rating.
    """
    sm_map: dict[tuple[str, str], dict] = {}
    cover_map: dict[tuple[str, str], str] = {}
    try:
        from modules.manhwa.db import get_supabase
        from modules.manhwa.storage.series_meta import load_series_meta_map

        sb = get_supabase()
        tks = [str(it.get("title_key") or "").strip() for it in to_send if it.get("title_key")]
        sm_map = load_series_meta_map(sb, tks)
        for (tk, src), r in sm_map.items():
            cover = str(r.get("cover") or "").strip()
            if cover:
                cover_map[(tk, src)] = cover
        # fallback: if no tks matched (empty title_key), load all covers
        if not cover_map:
            rows = sb.table("series_meta").select("title_key, source, cover").execute().data or []
            for r in rows:
                cover = str(r.get("cover") or "").strip()
                tk = str(r.get("title_key") or "").strip()
                src = str(r.get("source") or "").strip()
                if cover and tk:
                    cover_map[(tk, src)] = cover
    except Exception as e:
        logger.warn("dispatch: series_meta map build failed", err=str(e)[:120])
    return sm_map, cover_map


def _tag_subscribed(to_send: list[dict]) -> bool:
    """Re-derive `subscribed` from the live whitelist. Returns unavailable flag."""
    from modules.manhwa.core.text import slugify_title_key

    wl_unavailable = False
    try:
        from modules.manhwa.storage import whitelist as wl_store

        wl_rows = wl_store.load_whitelist_strict()
    except Exception as e:
        # Without the whitelist every item looks unsubscribed, and the raw feed
        # would announce chapters that belong to a subscribed series. Hold the
        # raw feed for this tick; whitelist channels still receive the items
        # that arrived already tagged.
        logger.error("dispatch: whitelist read failed — holding raw feed", exc=e)
        wl_rows = []
        wl_unavailable = True

    wl_pairs = {
        (slugify_title_key(w.get("title_key", "")), str(w.get("source") or "").lower())
        for w in wl_rows
    }
    for it in to_send:
        tk = slugify_title_key(str(it.get("title_key") or ""))
        src = str(it.get("source") or "").lower()
        if tk and (tk, src) in wl_pairs:
            it["subscribed"] = True
            continue
        if "subscribed" in it:
            continue
        # Untagged item: treat it as WHITELISTED, i.e. as it was before the raw
        # feed existed. Two reasons, and both are about not breaking a running
        # deployment:
        #
        #   * With no raw channel configured every channel is a whitelist
        #     channel, so routing untagged items to raw would send NOTHING at
        #     all — the failure test_dispatch_keys.py caught exactly that.
        #   * A caller that never heard of the split (an API endpoint, a manual
        #     resend, a backfill) passes items straight through; those are the
        #     same items the whitelist filter used to admit.
        #
        # The only way an item lands on raw is the raw feed's own tagging, which
        # is split_raw_and_whitelisted()/pipeline.py.
        it["subscribed"] = True
    return wl_unavailable



def dispatch(
    items: list[dict],
    channel_ids: list[str],
    instance_id: str,
    dry_run: bool = False,
    force: bool = False,
    guild_rows: list[dict] | None = None,
    skip_ceiling: bool = False,
    bypass_window: bool = False,
) -> int:
    """Send Discord embeds for whitelisted chapters.

    Dedupe: FCFS via fcfs_key (normalized title+chapter) in dispatch_history.
    No ceiling check needed — once a chapter is recorded, it's never re-sent.

    force=True -> skip the in-run claim guard (dispatch_claims) and the
    permanent URL guard. It does NOT skip the ceiling: the claimed path in
    pipeline.py sets force=True on every normal run, so tying the ceiling to
    force meant chapters below the ceiling were always notified.

    Correction to the paragraph above, which described the old behaviour:
    force=True skips the TRANSIENT guard only. The permanent
    dispatch_history URL guard is consulted on every path, force included —
    see `claimed_urls` below. The docstring used to claim this and the code
    did not do it.

    skip_ceiling=True is a separate, explicit escape hatch for a manual
    backfill or resend of a chapter that is genuinely below the ceiling.

    bypass_window=True treats every item as a real send, skipping the strict
    24h split. Without it a chapter older than 24h lands in to_backfill and
    only gets a silent ledger row -- no Discord post -- which is exactly why
    /dispatch/undelivered showed chapters for days and could never resend
    them. Only the manual resend path sets it.
    """
    if not getattr(settings, "DISCORD_ENABLED", True):
        logger.info("dispatch: skipped (DISCORD_ENABLED=false)")
        return 0
    if not items or not channel_ids:
        return 0
    if guild_rows is None:
        # Lazy import: the facade is what tests/handlers monkeypatch.
        from modules.manhwa.cron.dispatch_mod import load_guild_settings

        guild_rows = load_guild_settings()

    if bypass_window:
        to_send, to_backfill = list(items), []
    else:
        to_send, to_backfill = _split_send_backfill(items)

    # Sort: ascending chapter per series for ordered Discord delivery
    to_send.sort(
        key=lambda it: (
            it.get("title_key") or it.get("title") or "",
            (lambda m: float(m.group(1)) if m else float("inf"))(
                re.search(r"(\d+(?:\.\d+)?)", str(it.get("chapter") or ""))
            ),
        )
    )

    # NOTE: No cross-source dedup here — FCFS dedup against dispatch_history
    # handles duplicates. Cross-source dedup (keeping only latest chapter per
    # title_key) is wrong: when ch.9-25 are already dispatched, dedup keeps
    # ch.25, FCFS filters it, and ch.7-8 never get sent.

    # Backfill HTML-backlog items silently. Skipped on dry_run: it writes to
    # dispatch_history, so a dry run would mutate the very state it is meant to
    # leave untouched, and the rows would then be skipped as "already
    # dispatched" by the real run.
    _backfill(to_backfill, instance_id, dry_run)

    if not to_send:
        logger.info("dispatch: nothing to send")
        return 0

    # CEILING: skip chapters at or below the max already-dispatched chapter per
    # (title_key, source). Prevents re-dispatching old chapters when a higher
    # chapter was already sent (e.g. 125 sent, 110 re-appears).
    #
    # The max MUST be computed numerically, not by text ordering. Postgres
    # orders '9' > '20' as text, so a series that had shipped through ch.20
    # resolved to a ceiling of 9 (observed on
    # the-crimson-dragon-martial-emperor). Every chapter from 10..20 then passed
    # the ceiling check on every run. FCFS still skipped them because they are
    # in dispatch_history, so nothing was double-sent — but the guard was doing
    # nothing for exactly the series that needed it. A non-numeric chapter_title
    # (e.g. 'OVA') is ignored rather than raising, so it can never become a
    # bogus ceiling.
    #
    # How many items this batch carries per title_key. A legitimate renumbering
    # arrives as a small burst (e.g. 4000/4001/4002 together); a lone absurd
    # number is a bad label. Read by the sanity guard in _send_item.
    counts: dict[str, int] = {}
    for it in to_send:
        key = str(it.get("title_key") or "").strip()
        if key:
            counts[key] = counts.get(key, 0) + 1

    # All fcfs_keys already in dispatch_history — used by the ceiling guard to
    # distinguish "already sent, skip" from "never sent, allow through".
    # Scoped to this batch's title_keys — see _load_sent_keys_global.
    _batch_tks = [
        str(it.get("title_key") or "").strip() for it in to_send if it.get("title_key")
    ]
    sent_keys_global = _load_sent_keys_global(_batch_tks)
    ceilings = load_chapter_ceilings(
        list(
            {
                (str(it.get("title_key") or "").strip(), str(it.get("source") or "").strip())
                for it in to_send
                if it.get("title_key") and it.get("source")
            }
        )
    )

    # Reject junk URLs that don't match known source patterns. This MUST run
    # before every parallel list below (all_keys / all_urls / title_keys /
    # sources / chapter_titles) is built: claim_and_record pairs those lists
    # positionally, so a junk URL removed after all_keys was built shifted every
    # subsequent fcfs_key onto the wrong chapter_url and claimed the wrong key.
    to_send = _filter_junk_urls(to_send)

    # FCFS dedupe: skip chapters ALREADY NOTIFIED (in dispatch_history).
    # NOTE: we intentionally do NOT consult dispatch_claims here. The deep-queue
    # claim in pipeline.py (claim_recent_chapters_for_dispatch) and the
    # claim_and_record call below both write to dispatch_claims for THIS run —
    # if we treated those as "already sent" we'd skip every item we just claimed
    # and send nothing (the bug where cron ran forever with sent:0).
    # dispatch_history is the single source of truth for "actually notified".
    # HOWEVER, we MUST still check dispatch_history even when force=True —
    # force=True only bypasses the in-run claim guard (dispatch_claims), not the
    # permanent notification record. A previously-sent chapter must never be
    # re-notified even with force=True.
    # Same key builder as the ledger write below and as dispatch_watchdog.py:
    # title_key with dashes -> spaces. Using the display title here made the
    # in-run dedupe set disagree with dispatch_history, so a chapter already in
    # the ledger could pass the dedupe and be re-sent.
    #
    # Every list is derived from the SAME filtered `to_send`, so index i in
    # all_urls, all_keys, title_keys, sources and chapter_titles always refers
    # to the same item.
    sendable = [it for it in to_send if it.get("url")]
    all_keys: list[str] = [fcfs_key_for_item(it) for it in sendable]
    all_urls = [it.get("url", "") for it in sendable]
    title_keys = [it.get("title_key", "") for it in sendable]
    sources = [it.get("source", "") for it in sendable]
    chapter_titles = [it.get("chapter", "") for it in sendable]
    claimed_keys = _load_claimed_keys(all_keys)
    legacy_pairs = _load_legacy_pairs(to_send)

    # The permanent ledger is consulted on EVERY path, force included.
    #
    # force exists for exactly one caller: pipeline.py's deep-queue claim,
    # which has already written these URLs into dispatch_claims in the same
    # transaction and would otherwise see its own claims and skip every item
    # (the sent:0 bug). It is an escape from the TRANSIENT guard, nothing
    # more. The code used to also drop `claimed_urls` here, so whenever the
    # claim queue was healthy — i.e. on the normal cron path — a chapter that
    # was already sitting in dispatch_history could be re-notified. The
    # docstring two screens up claimed force did not do this; now it doesn't.
    claimed_urls = dispatch_store._already_dispatched(all_urls) if all_urls else set()

    # Claim once before channel loop (skipped when force=True — the deep-queue
    # claim already wrote these rows, so re-claiming would collide with them)
    if force:
        acq_map = {u: (u not in claimed_urls) for u in all_urls}
    else:
        acquired = dispatch_store.claim_and_record(
            all_urls,
            title_keys,
            sources,
            instance_id,
            chapter_titles=chapter_titles,
            fcfs_keys=all_keys,
        )
        acq_map = {u: ok for u, ok in zip(all_urls, acquired, strict=False)}

    sm_map, wl_cover_map = _build_series_meta_map(to_send)
    run = _RunState(
        instance_id=instance_id,
        dry_run=dry_run,
        skip_ceiling=skip_ceiling,
        sm_map=sm_map,
        wl_cover_map=wl_cover_map,
        ceilings=ceilings,
        counts=counts,
        sent_keys_global=sent_keys_global,
        claimed_keys=claimed_keys,
        claimed_urls=claimed_urls,
        legacy_pairs=legacy_pairs,
        acq_map=acq_map,
    )

    logger.info("dispatch: send-pass start", to_send=len(to_send), claimed_db=len(claimed_keys))

    # Two feeds, two channel sets. `to_send` carries `subscribed` per item from
    # collect()/split_raw_and_whitelisted(); dispatch re-derives it here from the
    # same (title_key, source) rule so an item that arrived without the tag is
    # still routed correctly. See split_dispatch_targets().
    from modules.manhwa.cron.dispatch_mod import split_dispatch_targets
    from modules.manhwa.services.dispatch.channels import source_whitelist_map

    raw_channels, _ = split_dispatch_targets(channel_ids)
    src_wl_map = source_whitelist_map()
    # Raw announcements are opt-in: with no raw channel configured every channel
    # is a whitelist channel, which is the previous single-feed behaviour.
    # Otherwise a raw feed would start publishing on a deployment that never
    # asked for it.
    has_raw = bool(raw_channels)
    wl_unavailable = _tag_subscribed(to_send)

    sent = 0
    for ch in channel_ids:
        _is_raw_ch = has_raw and ch in raw_channels
        if _is_raw_ch and wl_unavailable:
            # Fail safe: an unreadable whitelist must never be read as "no
            # subscriptions", or every whitelisted chapter lands on RAW.
            logger.info("dispatch: raw feed held (whitelist unreadable)", channel=ch)
            continue
        # Raw channel: only items NOT whitelisted (every new chapter, minus the
        # ones that already have a whitelist feed). Whitelist channel: the
        # opposite set. Splitting here rather than upstream means one batch and
        # one claim serve both feeds — a second pass would have to re-claim the
        # same URLs and fight the claim guard.
        #
        # `subscribed == (not _is_raw_ch)`: raw ⇒ subscribed is False,
        # whitelist ⇒ subscribed is True. Written as an equality rather than
        # "!= _is_raw_ch" because the two read the same but only this one is
        # obviously symmetric — the inverted form silently routes every
        # whitelisted chapter to raw and every stray one to whitelist.
        _feed = [it for it in to_send if bool(it.get("subscribed")) == (not _is_raw_ch)]
        # Per-source whitelist: a channel mapped to one source carries only
        # that source's whitelisted items, so shinigami and ikiru each get
        # their own thread instead of sharing one whitelist channel.
        _src = src_wl_map.get(ch)
        if _src and not _is_raw_ch:
            _feed = [it for it in _feed if str(it.get("source") or "").strip().lower() == _src]
        if not _feed:
            logger.info(
                "dispatch: feed empty",
                channel=ch,
                feed="raw" if _is_raw_ch else "whitelist",
            )
            continue
        # Per-guild filters (multi-server). Falls back to empty filters when the
        # channel has no guild_settings row (e.g. explicit channel_ids arg).
        gs_row = next((g for g in guild_rows if str(g.get("channel_id")) == str(ch)), {})
        type_filter = {
            t.strip().lower() for t in str(gs_row.get("type_filter") or "").split(",") if t.strip()
        }
        seen_key_run: set[str] = set()
        channel_failures: dict[str, int] = {}
        for it in _feed:
            outcome = _send_item(
                it,
                ch,
                _is_raw_ch,
                run,
                seen_key_run,
                channel_failures,
                type_filter,
            )
            if outcome in (_SENT, _DRY):
                sent += 1
            elif outcome == _BLOCKED:
                break

    # Post-loop: flush dispatch_history + update whitelist markers
    # claim queue is FOR UPDATE SKIP LOCKED in app/services/claim.py —
    # dispatch_claims is transient
    _flush_history(run, to_send)

    logger.info("dispatch: send-pass done", sent=sent, dry_run=dry_run)
    if sent and not dry_run:
        try:
            from modules.manhwa.services.audit import AuditAction, log_action

            log_action(
                AuditAction.DISPATCH,
                resource="dispatch",
                resource_id=f"{instance_id}",
                metadata={"sent": sent, "channels": len(channel_ids)},
            )
        except Exception:
            pass
    return sent
