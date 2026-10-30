"""Source collection orchestrator"""
from __future__ import annotations

import os
from datetime import datetime, timezone

# Freshness window for scraped chapters (hours). One source of truth for
# both the API-side adaptive pagination (collect_whitelisted_shinigami_chapters)
# and the post-collect row filter (pipeline._filter_fresh_window), so the two
# can never drift apart and silently disagree about what counts as fresh.
FRESH_WINDOW_HOURS = int(os.getenv("SCRAPE_FRESH_WINDOW_HOURS", "24") or 24)

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger
from modules.manhwa.core.text import slugify_title_key
from modules.manhwa.storage import health, whitelist as wl_store
from modules.manhwa.cron.collectors.common import _SOURCE_TIMEOUT, build_item
from modules.manhwa.cron.collectors.shinigami import _collect_shinigami_source
from modules.manhwa.cron.collectors.ikiru import _collect_ikiru_source
from modules.manhwa.cron.source_result import SourceResult
import time as _time

logger = get_logger("cron:collect")
health_store = health

# How many whitelisted shinigami series one tick may walk.
#
# The walk is serial and costs ~1.8s per series (measured against the live
# API), so 246 whitelisted series need ~452s — which is what made an rss-fetch
# tick take 116-153s and starve the single-worker queue until `update` never
# ran. The budget must therefore fit both _SOURCE_TIMEOUT (120s) and the tick
# interval; 40 series is ~72s, leaving headroom for the other collectors that
# run in the same tick.
#
# Override with SHINIGAMI_WALK_BUDGET for a one-off full sweep.
_WALK_BUDGET_PER_TICK = int(os.getenv("SHINIGAMI_WALK_BUDGET", "40") or 40)
_WALK_CURSOR_KEY = "beag:shinigami_walk_cursor"

# Last split of the collected batch into (raw, whitelist) counts. Purely
# telemetry: collect() has no other channel to report it, and "raw announced 0"
# is indistinguishable from "raw was never computed" without it.
_last_split: dict[str, int] = {"raw": 0, "whitelist": 0}


def last_split() -> dict[str, int]:
    """The raw/whitelist counts from the most recent collect() call."""
    return dict(_last_split)


def _load_walk_cursor() -> int:
    """Read the rotating walk cursor. Missing/unparsable restarts at 0.

    The walk is idempotent (chapters already sent are filtered by the
    latest_sent ceiling), so a reset costs a repeated pass, never a lost
    chapter.
    """
    try:
        from modules.manhwa.tasks.queue import _get_redis
        v = _get_redis().get(_WALK_CURSOR_KEY)
        n = int(v) if v is not None else 0
        return n if n >= 0 else 0
    except Exception:
        return 0


def _save_walk_cursor(cursor: int) -> None:
    """Persist the cursor. A failure only costs a repeated slice."""
    try:
        from modules.manhwa.tasks.queue import _get_redis
        _get_redis().set(_WALK_CURSOR_KEY, max(0, int(cursor)), ex=86400)
    except Exception as e:
        logger.debug("walk cursor save failed", err=str(e)[:120])


def collect_recent_chapters(
    with_whitelisted_shinigami: bool = False,
    source: str | None = None,
    fetch_meta: bool = True,
) -> tuple[list[dict], dict]:
    # Two independent inputs: the operator toggle, plus the auto-cooldown
    # written by save_source_health_map when consecutive_failures >= 3.
    from modules.manhwa.config import disabled_sources as _disabled_sources

    _disabled: set[str] = _disabled_sources()
    try:
        hm = health_store.load_source_health_map(settings.SOURCE_KEYS)
        _now = datetime.now(timezone.utc)
        for src, row in (hm or {}).items():
            du = row.get("disabled_until")
            if du:
                try:
                    if datetime.fromisoformat(du.replace("Z", "+00:00")) > _now:
                        _disabled.add(src)
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    items: list[dict] = []
    import time as _t
    _hm: dict[str, dict] = {}
    _now_iso = datetime.now(timezone.utc).isoformat()

    _latest_sent: dict[tuple[str, str], float] = {}
    try:
        from modules.manhwa.db import get_supabase as _gsb_ls
        _wl_ls = _gsb_ls().table("whitelist").select("title_key, source, latest_sent_chapter").execute()
        for _w in (_wl_ls.data or []):
            _tk = slugify_title_key(_w.get("title_key") or "")
            _src = _w.get("source") or ""
            try:
                _ls = float(_w.get("latest_sent_chapter") or 0)
            except (ValueError, TypeError):
                _ls = 0
            if _tk:
                _latest_sent[(_tk, _src)] = max(_latest_sent.get((_tk, _src), 0), _ls)
    except Exception as _e:
        logger.warn("collect: load latest_sent_chapter failed", err=str(_e)[:160])

    def _health_start(src: str) -> float:
        return _t.time()

    def _health_end(src: str, t0: float, ok: bool, err: str | None = None) -> None:
        rt = int((_t.time() - t0) * 1000)
        prev = _hm.get(src) or {}
        consec = 0 if ok else prev.get("consecutive_failures", 0) + 1
        text = (err or "").lower()
        if ok:
            status = "HEALTHY"
        elif any(x in text for x in ("429", "rate limit", "too many requests")):
            status = "RATE_LIMITED"
        elif any(x in text for x in ("403", "forbidden", "cloudflare", "blocked")):
            status = "BLOCKED"
        elif any(x in text for x in ("timeout", "timed out", "connection")):
            status = "DOWN"
        else:
            status = "DEGRADED"
        _hm[src] = {
            "status": status,
            "response_time_ms": rt,
            "successes_today": (prev.get("successes_today", 0) + 1) if ok else prev.get("successes_today", 0),
            "failures_today": (prev.get("failures_today", 0) + 1) if not ok else prev.get("failures_today", 0),
            "consecutive_failures": consec,
            "last_success_at": _now_iso if ok else prev.get("last_success_at"),
            "last_checked_at": _now_iso,
            "last_error": err if not ok else None,
        }
        try:
            from modules.manhwa.metrics_prometheus import track_rss_fetch
            if not ok:
                track_rss_fetch(source=src, item_count=0, error=str(err)[:80])
            else:
                track_rss_fetch(source=src, item_count=len(items))
        except Exception:
            pass

    import concurrent.futures

    def _try_collect(src: str) -> SourceResult:
        started = _time.monotonic()
        try:
            _src_items: list[dict] = []
            if src == "shinigami":
                _src_items = _collect_shinigami_source(_latest_sent, _disabled, fetch_meta)
            elif src == "ikiru":
                _src_items = _collect_ikiru_source(_latest_sent, _disabled, fetch_meta)
            return SourceResult.ok(src, _src_items, started)
        except Exception as exc:
            # ikiru upstream is chronically flaky (502/timeout) — downgrade to
            # debug so it doesn't spam warn. Other sources keep warn.
            if src == "ikiru":
                logger.debug("collect provider failed", source=src, err=str(exc)[:300])
            else:
                logger.warn("collect provider failed", source=src, err=str(exc)[:300])
            return SourceResult.failed(src, started, exc)

    _sources_to_run: list[str] = []
    for _src in ("shinigami", "ikiru"):
        if (source is None or source == _src) and _src not in _disabled:
            _sources_to_run.append(_src)

    if _sources_to_run:
        _t0_map: dict[str, float] = {}

        def _run_phase(sources: list[str]):
            if not sources:
                return
            _executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(sources))
            _futures: dict = {}
            for _src in sources:
                _t0_map[_src] = _health_start(_src)
                logger.info("collect start", source=_src)
                _futures[_executor.submit(_try_collect, _src)] = _src
            try:
                for _future in concurrent.futures.as_completed(_futures, timeout=_SOURCE_TIMEOUT):
                    _src = _futures[_future]
                    _t0 = _t0_map.get(_src, _health_start(_src))
                    try:
                        result = _future.result(timeout=5)
                        if not result.success:
                            _health_end(_src, _t0, False, result.error or "provider failed")
                            if _src == "ikiru":
                                logger.debug("collect failed", source=_src, err=result.error or "provider failed")
                            else:
                                logger.warn("collect failed", source=_src, err=result.error or "provider failed")
                            continue
                        items.extend(result.items)
                        _health_end(_src, _t0, True)
                        logger.info("collect done", source=_src, count=len(result.items), response_time_ms=result.latency_ms)
                    except Exception as e:
                        _health_end(_src, _t0, False, str(e)[:300])
                        if _src == "ikiru":
                            logger.debug("collect failed", source=_src, err=str(e)[:200])
                        else:
                            logger.warn("collect failed", source=_src, err=str(e)[:200])
            except concurrent.futures.TimeoutError:
                for _future, _src in _futures.items():
                    if not _future.done():
                        _health_end(_src, _t0_map.get(_src, _health_start(_src)), False, f"timeout after {_SOURCE_TIMEOUT}s")
                        if _src == "ikiru":
                            logger.debug("collect TIMEOUT", source=_src, timeout=_SOURCE_TIMEOUT)
                        else:
                            logger.warn("collect TIMEOUT", source=_src, timeout=_SOURCE_TIMEOUT)
                        try:
                            _future.cancel()
                        except Exception:
                            pass
            finally:
                try:
                    _executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    _executor.shutdown(wait=False)

        _run_phase(_sources_to_run)
    else:
        for _src in ("shinigami", "ikiru"):
            _hm[_src] = {"status": "disabled", "response_time_ms": 0, "successes_today": 0, "failures_today": 0, "consecutive_failures": 0, "last_success_at": None, "last_checked_at": _now_iso, "last_error": "cooldown"}

    # One fresh whitelist read per tick. The cron process is a separate
    # systemd unit from manhwa-api, so the 600s ttl_cache snapshot held HERE
    # is not invalidated when a title is added through the API process —
    # without a forced reload a freshly subscribed series keeps routing to
    # RAW for up to ten minutes.
    try:
        _wl = wl_store.load_whitelist_strict()
    except Exception as e:
        # Fail CLOSED: without the whitelist every chapter would route to RAW
        # and the whitelist feed would get nothing. Skip the tick instead.
        logger.error("collect: whitelist read failed, skipping tick", exc=e)
        return [], _hm

    if with_whitelisted_shinigami and "shinigami" not in _disabled:
        try:
            items.extend(collect_whitelisted_shinigami_chapters(_wl))
        except Exception as e:
            logger.warn("collect whitelisted shinigami failed", err=str(e)[:120])

    # Cross-source dedup: same title+chapter from multiple sources → keep the
    # WHITELISTED copy.
    #
    # collect_whitelisted_shinigami_chapters() appends its rows AFTER the
    # source sweep, so "keep the first occurrence" used to drop exactly the
    # copy that belonged to the subscribed series. The survivor came from a
    # source that was not subscribed, its (title_key, source) pair missed the
    # whitelist, and the chapter was announced on RAW while the whitelist
    # feed received nothing.
    items = dedup_prefer_whitelisted(items, _wl)

    # ── Subscribed split ──
    #
    # The raw feed announces EVERY new chapter; the whitelist feed announces
    # only series the operator subscribed to. Both read the same rows, so the
    # subscription flag is computed here, once, from the same (title_key,
    # source) rule filter_whitelisted() uses — a second, divergent matcher here
    # is how raw and whitelist came to disagree in the first place.
    #
    # The batch is re-ordered raw-first: dispatch() walks `to_send` in order
    # and keeps sending the first chapter it can, so leading with raw means a
    # whitelisted series still gets its notification even when the raw feed
    # produced a bigger burst.
    if with_whitelisted_shinigami:
        # Reuse the tick's snapshot: a second read here could disagree with
        # the one the dedup above used, and the two answers are what decide
        # which feed a chapter lands on.
        _raw, _wls = split_raw_and_whitelisted(items, _wl)
        _last_split.update({"raw": len(_raw), "whitelist": len(_wls)})
        items = _raw + _wls

    return items, _hm


def dedup_prefer_whitelisted(items: list[dict], whitelist: list[dict]) -> list[dict]:
    """Collapse cross-source duplicates, keeping the WHITELISTED copy.

    Same title+chapter can arrive from more than one source. The whitelisted
    shinigami rows are appended AFTER the source sweep, so a plain
    "keep the first occurrence" pass dropped exactly the copy that belonged
    to the subscribed series: the survivor came from a source nobody
    subscribed, its (title_key, source) pair missed the whitelist, and the
    chapter was announced on RAW while the whitelist feed got nothing.

    A whitelisted copy therefore always wins, regardless of arrival order.
    """
    from modules.manhwa.services.fcfs import fcfs_key as _fcfs
    from modules.manhwa.core.text import slugify_title_key as _slug_c

    _wl_pairs = {
        (_slug_c(w.get("title_key", "")), str(w.get("source") or "").lower())
        for w in (whitelist or [])
    }
    _by_key: dict[str, dict] = {}
    _key_is_wl: dict[str, bool] = {}
    _dup_count = 0
    for it in items:
        _fk = _fcfs(it.get("title", ""), it.get("chapter", ""))
        _is_wl = (
            _slug_c(it.get("title_key", "")),
            str(it.get("source") or "").lower(),
        ) in _wl_pairs
        if _fk not in _by_key:
            _by_key[_fk] = it
            _key_is_wl[_fk] = _is_wl
            continue
        _dup_count += 1
        if _is_wl and not _key_is_wl[_fk]:
            _by_key[_fk] = it
            _key_is_wl[_fk] = True
    if _dup_count:
        logger.info("collect: cross-source dedup", removed=_dup_count)
    return list(_by_key.values())


def filter_whitelisted(items: list[dict], whitelist: list[dict]) -> list[dict]:
    """Keep items whose (title_key, source) pair is whitelisted.

    Source-strict, matching the whitelist's own grain: `whitelist` has one row
    per (title_key, source), so subscribing a series on shinigami does not
    subscribe it on another source.

    This used to match on the title alone, on the theory that a series is one
    series and FCFS would pick whichever source reported first. In practice that
    sent notifications from sources nobody subscribed to: 18 of 160 dispatches
    (11%) were for a (title, source) pair absent from the whitelist. The user
    sees an embed for a series they only track on shinigami — and the
    per-source badge on /whitelist disagrees with the notification they got.

    claim_recent_chapters_for_dispatch() and the SQL claim join already work
    per (title_key, source); this function was the one path that did not, which
    is why the bug only showed on some dispatches.
    """
    allowed: set[tuple[str, str]] = set()
    for w in whitelist:
        wk = slugify_title_key(w.get("title_key", ""))
        src = str(w.get("source") or "").lower()
        if wk:
            allowed.add((wk, src))
    return [
        it
        for it in items
        if (slugify_title_key(it.get("title_key", "")), str(it.get("source") or "").lower())
        in allowed
    ]


def split_raw_and_whitelisted(
    items: list[dict], whitelist: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Split a batch into (raw, whitelisted) using the SAME rule dispatch uses.

    Returns the two lists, each item tagged `subscribed`. Raw is everything not
    whitelisted — the raw feed announces every new chapter, so a series nobody
    subscribed to still gets announced, with an "Add to whitelist" button on its
    embed. Whitelist is the opposite set.

    Both halves are computed by one pass over `items` against one set of pairs,
    so they cannot disagree about whether a (title_key, source) is subscribed.
    """
    pairs = {
        (slugify_title_key(w.get("title_key", "")), str(w.get("source") or "").lower())
        for w in whitelist
    }
    raw_items: list[dict] = []
    wl_items: list[dict] = []
    for it in items:
        tk = slugify_title_key(it.get("title_key", ""))
        src = str(it.get("source") or "").lower()
        it["subscribed"] = (tk, src) in pairs
        (wl_items if it["subscribed"] else raw_items).append(it)
    return raw_items, wl_items


def collect_whitelisted_shinigami_chapters(whitelist: list[dict]) -> list[dict]:
    import random
    ids: list[tuple[str, str, str]] = []
    seen_ids: set[str] = set()
    from modules.manhwa.db import get_supabase as _get_sb
    from modules.manhwa.sources import shinigami
    _sb = _get_sb()
    for w in whitelist:
        wk = slugify_title_key(w.get("title_key", ""))
        src = w.get("source")
        if src != "shinigami":
            continue
        v = w.get("url") or w.get("series_url") or w.get("permalink") or ""
        mid = None
        if 'shinigami.asia/' in v:
            seg = v.rstrip('/').split('/')[-1]
            if seg and '-' in seg:
                mid = seg
        if not mid:
            try:
                rc = (_sb.table("recent_chapters").select("series_url").eq("title_key", w.get("title_key", "")).eq("source", "shinigami").neq("series_url", "").limit(1).execute())
                if rc.data:
                    su = rc.data[0].get("series_url") or ""
                    if "shinigami.asia/series/" in su:
                        mid = su.rstrip("/").split("/")[-1]
            except Exception:
                pass
        if not mid:
            continue
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        ids.append((mid, wk, w.get("title") or wk.replace("_", " ").title()))

    # ── Bound the walk, and rotate through the rest ──
    #
    # This walk is SERIAL and costs ~1.8s per series against the live API.
    # Measured: 246 whitelisted series => ~452s. That is the single reason an
    # rss-fetch tick took 116-153s, which starved the worker (one job at a
    # time) until the `update` job never reached the front of the queue — the
    # 7-hour notification outage.
    #
    # The walk exists only to catch the chapters BETWEEN the latest_sent
    # ceiling and the newest one, because the latest-updates feed collapses a
    # batch release into a single row. So it does not need to cover everything
    # every tick: it takes a bounded slice and resumes from a stored cursor.
    # Worst case a middle chapter waits one full rotation, which is far better
    # than the walk being killed outright and nothing being covered.
    total = len(ids)
    if total == 0:
        return []
    budget = max(1, _WALK_BUDGET_PER_TICK)
    if total > budget:
        start = _load_walk_cursor() % total
        ids = [ids[(start + i) % total] for i in range(budget)]
        _save_walk_cursor((start + len(ids)) % total)
    else:
        _save_walk_cursor(0)

    items: list[dict] = []
    API_CHAPTER_LIMIT = 100
    # Freshness scan: fetch pages only while chapters are still inside the
    # window, so a series that releases 10 or 20 chapters in a day is fully
    # captured, while a quiet series stops at page 1. max_pages stays a hard
    # ceiling. No page count is hardcoded from observation.
    API_MAX_PAGES = int(os.getenv("SHINIGAMI_FRESH_MAX_PAGES", "5") or 5)
    _notified: dict[str, set[float]] = {}
    try:
        from modules.manhwa.db import get_supabase as _gsb2
        _sb2 = _gsb2()
        _wk_list = [wk for _, wk, _ in ids]
        if _wk_list:
            _dh = (_sb2.table("dispatch_history").select("title_key, source, chapter_title").in_("title_key", _wk_list).execute())
            for _row in (_dh.data or []):
                _tk = _row.get("title_key")
                _ct = _row.get("chapter_title")
                try:
                    _cn = float(_ct)
                except (ValueError, TypeError):
                    continue
                _notified.setdefault(f"{_tk}:{_row.get('source')}", set()).add(_cn)
    except Exception as _e:
        logger.warn("shinigami notified-history load failed", err=str(_e)[:120])
    for mid, wk, wtitle in ids:
        chapters = None
        for _att in range(5):
            try:
                chapters = shinigami.get_shinigami_chapters(
                    mid, per_page=API_CHAPTER_LIMIT, max_pages=API_MAX_PAGES, within_hours=FRESH_WINDOW_HOURS
                )
                break
            except Exception as e:
                import time as _bt
                if _att < 4:
                    _sleep = min(2.0 * (_att + 1), 10.0) + random.uniform(0, 1.0)
                    _bt.sleep(_sleep)
                    continue
                logger.warn("whitelisted shinigami api scrape failed", mid=mid, err=str(e)[:120])
        if not chapters:
            continue
        series_url = f"{settings.SHINIGAMI_PUBLIC_BASE}/series/{mid}"
        _sent = _notified.get(f"{wk}:shinigami") or set()
        for ch in chapters:
            num = ch.get("chapter_number") or ch.get("number") or ch.get("chapter")
            ch_id = ch.get("chapter_id") or ch.get("id")
            ch_url = f"{settings.SHINIGAMI_PUBLIC_BASE}/chapter/{ch_id}" if ch_id else (ch.get("url") or "")
            if not ch_url or num is None:
                continue
            if num in _sent:
                continue
            items.append(build_item(
                title=(wtitle or wk.replace("_", " ").title()).replace("’", "'"),
                title_key=wk,
                chapter=str(num),
                chapter_num=float(num) if str(num).replace(".", "", 1).isdigit() else 0,
                url=ch_url,
                source="shinigami",
                cover=None,
                series_url=series_url,
                chapter_url=ch_url,
                origin="",
                updated_time=ch.get("release_date") or ch.get("created_at") or "",
                release_date=ch.get("release_date") or "",
            ))
    return items
