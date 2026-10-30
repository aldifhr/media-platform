"""Cron dual-pass scrape pipeline (parity with lib/cron/dual-pass-scrape.ts).

Orchestrates: scrape sources -> filter whitelist -> enrich -> dispatch to Discord.

This module uses the new services layer (scraper_service, dispatch_service)
internally. Symbols from legacy modules (collect, enrich, dispatch_mod) are
re-exported for backward compatibility with existing call sites.
"""
from __future__ import annotations

import json
import time

from modules.manhwa.cron import collect, dispatch_mod, enrich as enrich_mod
from modules.manhwa.logger import get_logger
from modules.manhwa.storage import health, whitelist as wl_store

logger = get_logger("cron:dual-pass")

# alias used throughout (run_pipeline + callers reference health_store)
health_store = health

collect_recent_chapters = collect.collect_recent_chapters  # noqa: shim
filter_whitelisted = collect.filter_whitelisted  # noqa: shim
enrich = enrich_mod.enrich  # noqa: shim
dispatch = dispatch_mod.dispatch  # noqa: shim

# Freshness window for scraped chapters.
#
# collect_recent_chapters(with_whitelisted_shinigami=True) pulls up to
# API_CHAPTER_LIMIT (100) chapters per whitelisted series from the source API.
# With 182 shinigami series that is ~18.2k rows on EVERY rss-fetch cycle, of
# which only a few dozen are new — so batch_insert_recent_chapters spent
# ~150s re-upserting 16.5k stale rows to persist ~33 new ones. That held the
# single cron worker long enough to starve the `update` (dispatch) job, which
# then hit the dedup set on every tick.
#
# We only care about chapters released inside the freshness window; anything
# older was already offered for notification on an earlier cycle. Dispatch
# reads recent_chapters with hours=24, so a 24h window is exactly its own
# horizon — nothing it can act on gets dropped here.
#
# Single source of truth, shared with the API-side adaptive pagination in
# collect.py so the two stages can never disagree about what "fresh" means.
_FRESH_WINDOW_HOURS = collect.FRESH_WINDOW_HOURS


def _filter_fresh_window(items: list[dict], hours: int = _FRESH_WINDOW_HOURS) -> list[dict]:
    """Drop scraped chapters released before the freshness window.

    Mirrors batch_insert_recent_chapters' release_date resolution: prefer
    release_date, fall back to updated_time, and KEEP the row when neither
    parses — an unknown age must not silently discard a chapter.
    """
    if hours <= 0:
        return items
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    kept: list[dict] = []
    dropped_by_source: dict[str, int] = {}
    dropped_unknown = 0
    for it in items:
        ts = None
        for field in ("release_date", "updated_time"):
            raw = it.get(field)
            if isinstance(raw, str) and raw.strip():
                try:
                    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    break
                except (ValueError, TypeError):
                    continue
        if ts is None:
            # Unknown age → keep (never discard on missing data).
            kept.append(it)
            dropped_unknown += 1
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            kept.append(it)
        else:
            src = str(it.get("source") or "unknown")
            dropped_by_source[src] = dropped_by_source.get(src, 0) + 1
    if dropped_by_source:
        logger.info(
            "pipeline: fresh-window filter",
            window_hours=hours,
            kept=len(kept),
            dropped=sum(dropped_by_source.values()),
            by_source=dropped_by_source,
            kept_unknown_age=dropped_unknown,
        )
    return kept

def run_pipeline(channel_ids: list[str] | None = None, do_dispatch: bool = True, dry_run: bool = False, action: str = "update") -> dict:
    """Full dual-pass run. Returns stats dict.

    do_dispatch=False → scrape + persist only (rss-fetch mode, no Discord send).
    do_dispatch=True  → read from recent_chapters (RSS is the data provider), dispatch whitelisted.

    dry_run=True → when do_dispatch=True, compute the FULL dispatch (match +
    FCFS) but do NOT send to Discord and do NOT write dispatch_history/
    dispatch_claims. Returns accurate `sent`/`matched` counts for what a live
    run WOULD do, without spamming the channel or polluting the DB. The FCFS
    guard still runs against the real dispatch_history, so it predicts skips
    correctly. Ignored when do_dispatch=False (fetch mode never sends anyway).
    """
    start = time.time()
    instance_id = f"be-ag-py-{int(start)}"
    _use_claimed = False  # set True only when the deep-queue claim path is used

    # Parse source from action string (e.g., "rss-fetch:shinigami" → source="shinigami")
    source = None
    if ":" in action:
        action, source = action.split(":", 1)

    logger.info("pipeline start", action=action, source=source or "all", do_dispatch=do_dispatch, instance=instance_id)
    # ── Retry drain: check for pending failed inserts from previous run ──
    try:
        from modules.manhwa.tasks import _get_redis as _gr3
        _pending = _gr3().get("beag:failed_inserts:pending")
        if _pending:
            _info = json.loads(_pending)
            logger.warn("pipeline: previous run had failed inserts — retry via next scrape", **_info)
            _gr3().delete("beag:failed_inserts:pending")
    except Exception:
        pass
    try:
        # ── Per-source health telemetry ──
        _health_map: dict = {}
        if not do_dispatch:
            # Full telemetry from the actual scrape
            items, _health_map = collect.collect_recent_chapters(
                with_whitelisted_shinigami=True,
                source=source,
                fetch_meta=False,  # metadata enrichment runs in scheduled enrich job
            )
            # PERF: persist per-source health so /status shows
            # real telemetry instead of a stale row. collect_recent_chapters
            # builds _hm but never saved it before — a source's disabled_until
            # stayed frozen from an old failure, making /status show "cooldown".
            try:
                health_store.save_source_health_map(_health_map)
            except Exception as _he:
                logger.warn("collect health persist failed", err=str(_he)[:160])
            items = _filter_fresh_window(items, hours=_FRESH_WINDOW_HOURS)
            enriched_all = enrich_mod.enrich(items, persist_cache=True, skip_api=True)
            insert_stats = recent_chapters.batch_insert_recent_chapters(enriched_all)
            if insert_stats.get("failed", 0):
                logger.error("pipeline: batch_insert partial failure", **insert_stats)
                try:
                    from modules.manhwa.metrics_prometheus import PARTIAL_INSERT_FAILURES
                    PARTIAL_INSERT_FAILURES.labels(action=action, source=source or "all").inc()
                except Exception:
                    pass
                # Save failed rows for retry on next run
                try:
                    from modules.manhwa.tasks import _get_redis as _gr2
                    r = _gr2()
                    r.setex("beag:failed_inserts:pending", 3600, json.dumps({
                        "action": action,
                        "source": source or "all",
                        "failed": insert_stats["failed"],
                        "ts": time.time(),
                    }))
                except Exception:
                    pass
        else:
            # Dispatch mode: deep queue claim (FOR UPDATE SKIP LOCKED) — atomic whitelisted claim.
            _health_map = _probe_source_health()
            _use_claimed = False
            try:
                _wl_for_claim = wl_store.load_whitelist_strict()
                # Single seam via dispatch_service (was direct storage call)
                from modules.manhwa.services.dispatch.service import dispatch_service as _ds_claim
                _claimed = _ds_claim.claim_for_dispatch(whitelist=_wl_for_claim, hours=24, limit=500)
                if _claimed:
                    items = _claimed
                    enriched_all = enrich_mod.enrich(items, persist_cache=False)
                    _use_claimed = True
                else:
                    raise ValueError("no claimed rows, fallback")
            except Exception as e:
                logger.debug("claim queue unavailable — nothing to dispatch", err=str(e)[:80])
                items = []
                enriched_all = []
                _use_claimed = False
        # Persist health (both modes)
        try:
            health_store.save_source_health_map(_health_map)
        except Exception as e:
            logger.error("save source health failed", exc=e)

        if do_dispatch:
            # Auto-release STUCK claims (created >15m ago but never completed).
            try:
                from datetime import datetime, timezone, timedelta
                from modules.manhwa.storage import dispatch as _ds
                cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
                _ds.unclaim_stale(cutoff)
            except Exception as e:
                logger.warn("unclaim_stale failed", err=str(e)[:160])
            try:
                whitelist = wl_store.load_whitelist_strict()
            except Exception as e:
                # Cannot tell subscribed from not-subscribed. Announcing now
                # would push whitelisted chapters onto the RAW feed, so hold
                # the whole tick: dispatch_history has no rows for these
                # chapters yet, so the next tick picks them up unchanged.
                logger.error("pipeline: whitelist read failed — skipping dispatch", exc=e)
                health.write_cron_status("degraded", duration=round(time.time() - start, 1))
                return {
                    "sent": 0, "skipped": 0, "failed": 0,
                    "error": "whitelist_unavailable", "dispatched": False,
                }
            # Both feeds need EVERY item: the raw feed is "all new chapters",
            # the whitelist feed is "whitelisted new chapters". Filtering to the
            # whitelist here would starve raw entirely — it is the feed that
            # exists precisely for series not on the whitelist.
            #
            # The whitelist filter still has to be applied somewhere, or a
            # whitelisted series would be announced on the raw feed too and
            # carry an "Add to whitelist" button for a series already added.
            # dispatch() applies it per channel (split_dispatch_targets +
            # the subscribed flag), which is the one place that knows which
            # channel is which.
            to_dispatch = enriched_all
            # Tag the feed each item belongs to, using the SAME (title_key,
            # source) rule dispatch() re-derives it with. `subscribed` is what
            # dispatch reads to route, so it must be present even for items
            # that came through a path which never called
            # split_raw_and_whitelisted (e.g. the deep-queue claim).
            _wl_pairs = {
                (collect.slugify_title_key(w.get("title_key", "")), str(w.get("source") or "").lower())
                for w in whitelist
            }
            for _it in to_dispatch:
                _itk = collect.slugify_title_key(str(_it.get("title_key") or ""))
                _its = str(_it.get("source") or "").lower()
                _it["subscribed"] = bool(_itk) and (_itk, _its) in _wl_pairs
            channels = channel_ids or dispatch_mod._load_channels()
            # When the deep-queue claim path (_use_claimed) was used, the URLs
            # were ALREADY written to dispatch_claims by
            # claim_recent_chapters_for_dispatch() in the same transaction.
            # Calling dispatch() with the default claim guard would re-check
            # dispatch_claims, see its own just-written claims, and skip EVERY
            # item (sent:0) — the bug where cron ran forever with dispatched:true
            # but nothing was sent. force=True here only bypasses the duplicate
            # claim+guard; dispatch() still writes dispatch_history on success
            # so FCFS dedupe across future runs keeps working.
            _dispatch_force = bool(_use_claimed)
            sent = dispatch_mod.dispatch(to_dispatch, channels, instance_id, dry_run=dry_run, force=_dispatch_force) if to_dispatch else 0
            # Drain the failed-dispatches queue (transient Discord failures)
            # on every dispatch run, so a hiccup doesn't lose notifications.
            retry_stats: dict = {}
            if not dry_run:
                try:
                    from modules.manhwa.services.dispatch.retry import retry_failed_dispatches
                    retry_stats = retry_failed_dispatches(channels)
                except Exception as e:
                    logger.error("pipeline retry_failed failed", exc=e)
        else:
            sent = 0
            retry_stats = {}

        duration = round(time.time() - start, 1)
        _status = "ok"
        if _health_map:
            _vals = list(_health_map.values())
            _has_ok = any((v.get("status") or "").upper() in ("HEALTHY","OK") for v in _vals)
            _has_fail = any((v.get("status") or "").upper() in ("DEGRADED","DOWN","FAILED","BLOCKED","RATE_LIMITED") for v in _vals)
            if _has_fail and _has_ok:
                _status = "partial"
            elif _has_fail and not _has_ok:
                _status = "failed"
        stats = {
            "sent": sent,
            "skipped": 0,
            "failed": 0,
            "guilds": 1,
            "duration": duration,
            "fetched": len(items),
            "matched": len(to_dispatch) if do_dispatch else 0,
            "dispatched": do_dispatch,
            "retry_failed": retry_stats,
            "status": _status,
            "sources": {k: v.get("status") for k, v in _health_map.items()},
        }
        logger.info("Cron completed", **stats)
        health.write_cron_status(_status, chapters_sent=sent, matched=stats.get("matched", 0), duration=duration)

        return stats
    except Exception as e:
        logger.error("pipeline error", exc=e)
        health.write_cron_status("error", duration=round(time.time() - start, 1))
        return {"sent": 0, "skipped": 0, "failed": 1, "error": "internal error", "dispatched": do_dispatch}

# Imports kept at bottom to avoid circular import at module load:
# pipeline imports collect/enrich/dispatch_mod, which don't import pipeline.
from modules.manhwa.storage import recent_chapters  # noqa: E402

# Health-probe cache (PERF-01 #1): probing is on the dispatch critical path.
# Two serial HTTP probes with 15s timeouts = up to 30s stall when upstream is
# slow. Cache the result for a short window so back-to-back cron runs reuse it
# instead of re-probing every time.
_HEALTH_PROBE_CACHE: dict = {}
_HEALTH_PROBE_CACHE_TTL = 120.0  # seconds

# Whitelist cache: REMOVED — load_whitelist() already has @ttl_cache(ttl=600) in whitelist.py
# This was a double-cache. Call wl_store.load_whitelist() directly.

def _probe_source_health(force: bool = False) -> dict:
    """Lightweight per-source health probe for dispatch-mode cron runs.

    Results are cached for _HEALTH_PROBE_CACHE_TTL seconds so consecutive
    cron runs don't each pay up to 2×15s in serial probes. Probes run
    concurrently (ThreadPoolExecutor) so a single slow source can't block
    the other — worst case is one timeout, not two stacked.
    """
    import time as _t
    from datetime import datetime, timezone
    from concurrent.futures import ThreadPoolExecutor
    from modules.manhwa.config import settings as _s

    _now = datetime.now(timezone.utc).isoformat()

    # Serve from cache if fresh and not forced.
    if not force:
        _cached = _HEALTH_PROBE_CACHE.get("_map")
        _ts = _HEALTH_PROBE_CACHE.get("_ts", 0)
        if _cached is not None and (_t.time() - _ts) < _HEALTH_PROBE_CACHE_TTL:
            return _cached

    from curl_cffi import requests as cffi_req

    probes = {
        "shinigami": str(_s.SECONDARY_SOURCE_URL).rstrip("/") + "/v1/manga/list?page=1&page_size=1&is_update=true&sort=latest",
        "ikiru": str(_s.IKIRU_API_BASE).rstrip("/") + "/api/public/manga/project?page=1&layout=vertical&limit=1",
    }
    _probe_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        from modules.manhwa.storage import health as _hstore
        prev_map = _hstore.load_source_health_map(list(probes.keys()))
    except Exception:
        prev_map = {}

    def _probe_one(src: str, url: str) -> tuple[str, dict]:
        # The network call below is already retry-guarded, but an unexpected
        # error anywhere in this function would escape through
        # ThreadPoolExecutor.map and drop every source's health status, not
        # just this one. Isolate: a source that cannot even be probed reports
        # degraded rather than vanishing from the map.
        try:
            return _probe_one_inner(src, url)
        except Exception as _pe:
            logger.warn("health probe failed for one source — reporting degraded", src=src, err=str(_pe)[:160])
            return src, {
                "status": "degraded",
                "response_time_ms": 0,
                "successes_today": 0,
                "failures_today": 1,
                "consecutive_failures": 1,
                "last_success_at": None,
                "last_checked_at": _now,
                "last_error": str(_pe)[:300],
            }

    def _probe_one_inner(src: str, url: str) -> tuple[str, dict]:
        _prev = prev_map.get(src) or {}
        _prev_consec = int(_prev.get("consecutive_failures") or 0)
        t0 = _t.time()
        last_err = None
        ok = False
        rt = 0
        # Confirm a failure with up to 2 attempts so a single transient
        # timeout (curl_cffi/Cloudflare blip) doesn't flap the source
        # healthy<->degraded every cron tick.
        for _attempt in range(2):
            try:
                r = cffi_req.get(url, headers=_probe_headers, impersonate="chrome", timeout=5)
                rt = int((_t.time() - t0) * 1000)
                if r.status_code < 500:
                    ok = True
                    last_err = None
                    break
                else:
                    last_err = f"HTTP {r.status_code}"
            except Exception as e:
                rt = int((_t.time() - t0) * 1000)
                last_err = str(e)[:300]
        logger.info("[health-probe]", src=src, status="ok" if ok else "fail", rt_ms=rt)
        return src, {
            "status": "healthy" if ok else "degraded",
            "response_time_ms": rt,
            "successes_today": 1 if ok else 0,
            "failures_today": 0 if ok else 1,
            "consecutive_failures": 0 if ok else (_prev_consec + 1),
            "last_success_at": _now if ok else None,
            "last_checked_at": _now,
            "last_error": None if ok else last_err,
        }

    out: dict = {}
    # Concurrent probes: max one timeout (15s), not two stacked (30s).
    with ThreadPoolExecutor(max_workers=len(probes)) as _ex:
        for _src, _res in _ex.map(lambda kv: _probe_one(*kv), probes.items()):
            out[_src] = _res

    try:
        from modules.manhwa.cron.source_alert import alert_source_transitions
        alert_source_transitions(prev_map, out)
    except Exception as _ae:
        logger.warn("source alert dispatch failed", err=str(_ae)[:160])

    _HEALTH_PROBE_CACHE["_map"] = out
    _HEALTH_PROBE_CACHE["_ts"] = _t.time()
    return out