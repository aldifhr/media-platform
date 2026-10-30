from __future__ import annotations

import json
import threading
import time as _time


from modules.manhwa.tasks.queue import _get_redis, _cleanup_orphaned_set_entries, QUEUE_KEY, DLQ_KEY, CRON_QUEUE_KEY, CRON_QUEUE_SET, QUEUE_PROCESSING_KEY, CRON_PROCESSING_KEY, CRON_PROCESSING_TS
from modules.manhwa.tasks.retention import _retention_loop
from modules.manhwa.logger import get_logger

logger = get_logger("tasks.lifecycle")

_worker_thread: threading.Thread | None = None
_retention_thread: threading.Thread | None = None
_stop = threading.Event()

def do_add(item: dict) -> None:
    """Process one add-to-whitelist job directly (no Redis)."""
    from modules.manhwa.storage import whitelist
    from modules.manhwa.core.text import normalize_title_key
    title = item["title"]
    url = item["url"]
    tk = normalize_title_key(title)
    res = whitelist.add_whitelist_entries(
        [{"title": title, "title_key": tk, "source": "shinigami", "url": url, "series_url": url}]
    )
    logger.info("add done", title=title, status=res.get("status"))

def _process(payload: dict) -> bool:
    """Process one job. Returns True on success, False on failure."""
    try:
        if payload.get("kind") == "add":
            do_add(payload)
        else:
            logger.warn("unknown job kind", kind=payload.get("kind"))
        return True
    except Exception as e:
        logger.error("task failed", kind=payload.get("kind"), exc=e)
        return False

def run_cron_inline(action: str) -> None:
    """Run pipeline inline (used by cron worker when Redis is down)."""
    is_scrape = action.startswith("rss-fetch")
    source = action.split(":", 1)[1] if ":" in action else None
    do_dispatch = not is_scrape

    if action in ("enrich", "enrich-missing", "enrich-refresh"):
        from modules.manhwa.cron.enrich.resync import enrich_recent_chapters, enrich_stale_series_meta
        if action == "enrich":
            stats = enrich_recent_chapters(limit=100)
        elif action == "enrich-missing":
            stats = enrich_recent_chapters(limit=100, miss_only=True)
        else:
            stats = enrich_stale_series_meta(stale_days=7, limit=50)
        logger.info("cron enrich done", action=action, stats=stats)
        return

    if action == "failed-retry":
        from modules.manhwa.services.dispatch.retry import retry_failed_dispatches
        result = retry_failed_dispatches()
        logger.info("cron failed-retry done", **result)
        return

    if action == "dispatch-alert":
        from modules.manhwa.cron.dispatch_alert import check_and_alert_failed_dispatches
        check_and_alert_failed_dispatches()
        return

    if action == "gap-detect":
        from modules.manhwa.cron.gap_detector import maybe_alert_gaps
        maybe_alert_gaps()
        return

    if action == "watchdog":
        from modules.manhwa.cron.watchdog import check_and_alert
        check_and_alert()
        return

    if action == "dispatch-watchdog":
        # Separate from "watchdog" on purpose: that one reports on dispatch LAG
        # per whitelisted series, this one answers "is the pipeline alive and is
        # anything eligible but undelivered". Different questions, different
        # cadence.
        from modules.manhwa.cron.dispatch_watchdog import check_dispatch_starvation
        result = check_dispatch_starvation()
        logger.info("cron dispatch-watchdog done", **result)
        return

    if action == "dashboard-snapshot":
        from modules.manhwa.api.admin.stats import build_snapshot_sync
        from modules.manhwa.storage import health
        health.write_dashboard_snapshot(build_snapshot_sync())
        return

    if action == "whitelist-enrich":
        from modules.manhwa.cron.enrich.whitelist import enrich_all_whitelist
        enrich_all_whitelist()
        return

    if action == "retention":
        from modules.manhwa.db import get_supabase
        from modules.manhwa.storage import recent_chapters
        from datetime import datetime, timezone, timedelta
        # All windows come from modules.manhwa.tasks.retention — the single source of truth.
        # These used to be hardcoded here (72h for recent_chapters, 2 days for
        # cron_run_status) while retention.py declared 7 days for both. Both
        # paths run hourly, the tighter one always won, and the constants in
        # retention.py were therefore decorative: raising them changed nothing.
        from modules.manhwa.tasks.retention import (
            _DISPATCH_HISTORY_RETENTION_DAYS as _LEDGER_DAYS,
            _RECENT_CHAPTERS_RETENTION_DAYS as _RC_DAYS,
            _CRON_RUN_STATUS_RETENTION_DAYS as _CRON_DAYS,
        )
        recent_chapters.prune_older_than(_RC_DAYS * 24)
        recent_chapters.prune_dispatch_history_older_than(_LEDGER_DAYS * 24)
        get_supabase().table("cron_run_status").delete().lt(
            "created_at", (datetime.now(timezone.utc) - timedelta(days=_CRON_DAYS)).isoformat()
        ).execute()
        return

    if action == "vseries-refresh":
        from modules.manhwa.db import q as _q
        try:
            _q("REFRESH MATERIALIZED VIEW CONCURRENTLY v_series", [])
            logger.info("cron vseries-refresh done")
        except Exception as e:
            logger.warn("cron vseries-refresh failed", err=str(e)[:120])
        return

    if action in ("health", "sync-meta", "dispatch"):
        logger.warn("cron inline rejected", action=action)
        return

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            from modules.manhwa.cron.pipeline import run_pipeline
            run_pipeline(action=action, do_dispatch=do_dispatch)
            return
        except Exception as e:
            if attempt < max_attempts:
                wait = 2 ** attempt
                logger.warn("cron run failed, retrying", action=action, attempt=attempt, wait=wait, err=str(e)[:120])
                _time.sleep(wait)
            else:
                logger.error("cron run failed, moving to DLQ", action=action, attempts=max_attempts, exc=e)
                try:
                    _get_redis().rpush(DLQ_KEY, json.dumps({"action": action, "error": str(e)[:200], "attempts": max_attempts}))
                except Exception:
                    pass
    if is_scrape and source:
        try:
            from datetime import datetime, timezone
            _get_redis().set(
                f"cron:last_scrape:{source}",
                datetime.now(timezone.utc).isoformat(),
                ex=3600,
            )
        except Exception:
            pass

def _recover_processing() -> None:
    """On startup, move any orphaned processing jobs back to main queue (crash recovery)."""
    try:
        r = _get_redis()
        for proc_key, main_key in [(CRON_PROCESSING_KEY, CRON_QUEUE_KEY), (QUEUE_PROCESSING_KEY, QUEUE_KEY)]:
            while True:
                # lmove (Redis 6.2+) preserves FIFO: pop from the processing
                # list's TAIL and push to the main list's HEAD, which is where
                # enqueue_cron LPUSHes and the worker BRPOPLPUSHes from. The
                # older rpoplpush pushed to the head of a list the worker reads
                # from the tail, so recovered jobs were re-ordered behind
                # everything already queued.
                try:
                    job = r.lmove(proc_key, main_key, "RIGHT", "LEFT")
                except AttributeError:
                    job = r.rpoplpush(proc_key, main_key)
                if not job:
                    break
                logger.debug("recovered orphaned job from processing", queue=main_key)
                if main_key == CRON_QUEUE_KEY:
                    try:
                        r.sadd(CRON_QUEUE_SET, job)
                    except Exception:
                        pass
    except Exception as e:
        logger.warn("processing recovery failed", err=str(e)[:120])

# A cron job stuck in processing longer than this is presumed dead: the
# longest legitimate action (rss-fetch + dispatch pipeline) finishes in
# minutes, so 15 min means the worker hung or crashed mid-job.
_CRON_STALE_PROCESSING_S = 900


def _reap_stale_cron_processing() -> None:
    """Move cron jobs whose worker died mid-job back to the queue.

    BRPOPLPUSH leaves a job in the processing list until it is acked. If the
    worker process dies (or hangs) between pop and ack, the job sits there
    forever and its slot is blocked — _recover_processing only runs at
    startup, so a job orphaned at 3am waits until the next deploy. This
    reaper runs periodically and compares each processing entry's recorded
    start time against the threshold.
    """
    try:
        r = _get_redis()
        stale = r.hgetall(CRON_PROCESSING_TS) or {}
        if not stale:
            return
        now = _time.time()
        for payload, started_raw in list(stale.items()):
            try:
                started = float(started_raw)
            except (TypeError, ValueError):
                started = 0.0
            if now - started < _CRON_STALE_PROCESSING_S:
                continue
            # Only reap if the job is actually still sitting in processing
            # (it may have finished and the HDEL raced with this sweep).
            still_processing = r.lrange(CRON_PROCESSING_KEY, 0, -1) or []
            if payload not in still_processing:
                r.hdel(CRON_PROCESSING_TS, payload)
                continue
            # Move it back to the head of the main queue (FIFO: it was the
            # oldest, so it goes back to the front) and drop the timestamp.
            r.hdel(CRON_PROCESSING_TS, payload)
            try:
                r.lrem(CRON_PROCESSING_KEY, 1, payload)
            except Exception:
                pass
            try:
                r.lpush(CRON_QUEUE_KEY, payload)
                r.sadd(CRON_QUEUE_SET, payload)
            except Exception:
                pass
            logger.error(
                "cron job reaped from stale processing — worker died mid-job",
                payload=payload[:200],
                stuck_for_s=round(now - started, 1),
            )
    except Exception as e:
        logger.warn("stale cron processing reaper failed", err=str(e)[:120])


def run_cron_worker() -> None:
    """Blocking worker for the cron queue (ROLE=cron process only). Crash-safe via processing list."""
    logger.info("cron worker started")
    _recover_processing()
    _cleanup_orphaned_set_entries()
    _cleanup_counter = 0
    while not _stop.is_set():
        try:
            r = _get_redis()
            # Use brpoplpush if available, fallback to blpop+lpush for mock compat
            try:
                raw = r.brpoplpush(CRON_QUEUE_KEY, CRON_PROCESSING_KEY, timeout=5)
                result = (CRON_QUEUE_KEY, raw) if raw else None
            except AttributeError:
                result = r.blpop(CRON_QUEUE_KEY, timeout=5)
                if result:
                    _k, _raw = result
                    raw = _raw
                    try:
                        r.lpush(CRON_PROCESSING_KEY, raw)
                    except Exception:
                        pass
                    result = (_k, raw)
        except Exception as e:
            logger.debug("redis unavailable in cron worker, retrying", err=str(e)[:160])
            _stop.wait(10)
            continue
        if not result:
            continue
        _key, raw = result if isinstance(result, (list, tuple)) else (CRON_QUEUE_KEY, result)
        # dedup set cleanup — job left queue, remove from set so future enqueue allowed
        try:
            _get_redis().srem(CRON_QUEUE_SET, raw)
        except Exception as e:
            logger.debug("srem dedup cleanup failed (will retry via orphan sweep)", err=str(e)[:120])
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("bad cron payload, dropping", raw=raw[:200])
            try:
                _get_redis().lrem(CRON_PROCESSING_KEY, 1, raw)
            except Exception:
                pass
            continue
        # Record start time so the reaper can tell a running job from a dead
        # one. Deleted in the finally below on every exit path.
        try:
            _get_redis().hset(CRON_PROCESSING_TS, raw, str(_time.time()))
        except Exception:
            pass
        _action = item.get("action", "update")
        try:
            run_cron_inline(_action)
        except Exception as e:
            # A failed cron job used to be logged and then acked — gone, with
            # no retry and no trace. Push it to the DLQ and write a failure
            # row to cron_run_status so the heartbeat table shows it.
            logger.error("cron worker action failed — moving to DLQ", action=_action, err=str(e)[:300])
            try:
                _get_redis().rpush(DLQ_KEY, json.dumps({"action": _action, "error": str(e)[:200], "source": item.get("source", "")}))
            except Exception:
                pass
            try:
                from modules.manhwa.storage.health import write_cron_status
                write_cron_status(status="error")
            except Exception:
                pass
        finally:
            try:
                _get_redis().hdel(CRON_PROCESSING_TS, raw)
            except Exception:
                pass
            try:
                _get_redis().lrem(CRON_PROCESSING_KEY, 1, raw)
            except Exception as e:
                logger.warn("lrem processing ack failed (job stays in proc, recovered on restart)", err=str(e)[:120])
        _cleanup_counter += 1
        if _cleanup_counter >= 100:
            _cleanup_counter = 0
            _cleanup_orphaned_set_entries()
        # Reap dead jobs roughly once a minute (the BRPOPLPUSH timeout is 5s,
        # so ~12 idle iterations between sweeps).
        _cleanup_counter % 12 == 0 and _reap_stale_cron_processing()

def get_cron_status() -> dict:
    """Snapshot of the internal cron scheduler for the /cron monitor page.

    Every interval is read from the scheduler's own constants — hardcoding them
    here silently lied (it reported a 600s scrape interval while the scheduler
    ran at 300s, and listed only shinigami after two more sources were added).
    Add a source or retune an interval and this follows automatically.
    """
    from datetime import datetime, timezone
    from modules.manhwa.tasks import scheduler as _sched

    status: dict = {
        "scheduler_alive": bool(_sched._SCHED_THREAD and _sched._SCHED_THREAD.is_alive()),
        "source_interval_s": _sched._SOURCE_INTERVAL_S,
        "dispatch_interval_s": _sched._DISPATCH_INTERVAL_S,
        "enrich_interval_s": _sched._ENRICH_INTERVAL_S,
        "enrich_missing_interval_s": _sched._ENRICH_MISSING_INTERVAL_S,
        "enrich_refresh_interval_s": _sched._ENRICH_REFRESH_INTERVAL_S,
        "sources": list(_sched._RSS_SOURCES),
        "now": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = _get_redis()
        status["redis_up"] = bool(r.ping())
        status["queue_depth"] = int(r.llen(CRON_QUEUE_KEY))
    except Exception:
        status["redis_up"] = False
        status["queue_depth"] = -1
    status["is_processing"] = bool(
        status.get("scheduler_alive") and status.get("queue_depth", 0) > 0
    )
    try:
        r = _get_redis()
    except Exception:
        r = None
    src_status: dict[str, object] = {}
    try:
        from modules.manhwa.db import get_supabase
        sb = get_supabase()
        for src in _sched._RSS_SOURCES:
            last = None
            if r is not None:
                try:
                    hb = r.get(f"cron:last_scrape:{src}")
                    if hb:
                        last = hb.decode() if isinstance(hb, bytes) else str(hb)
                except Exception:
                    pass
            if not last:
                try:
                    rows = (
                        sb.table("recent_chapters")
                        .select("updated_time")
                        .eq("source", src)
                        .order("updated_time", desc=True)
                        .limit(1)
                        .execute()
                        .data
                        or []
                    )
                    last = rows[0].get("updated_time") if rows else None
                except Exception:
                    last = None
            next_in = None
            if last:
                try:
                    lt = datetime.fromisoformat(last) if isinstance(last, str) else last
                    if lt.tzinfo is None:
                        lt = lt.replace(tzinfo=timezone.utc)
                    elapsed = (datetime.now(timezone.utc) - lt).total_seconds()
                    next_in = max(0, _sched._SOURCE_INTERVAL_S - elapsed)
                except Exception:
                    next_in = None
            src_status[src] = {"last_scrape": last, "next_scrape_in_s": next_in}
    except Exception:
        pass
    status["per_source"] = src_status
    return status

def worker_loop() -> None:
    """Blocking worker: pops jobs, processes them, retries on failure. Crash-safe via processing list."""
    _fail_streak = 0
    _recover_processing()
    while not _stop.is_set():
        try:
            r = _get_redis()
            try:
                raw = r.brpoplpush(QUEUE_KEY, QUEUE_PROCESSING_KEY, timeout=5)
                result = (QUEUE_KEY, raw) if raw else None
            except AttributeError:
                result = r.blpop(QUEUE_KEY, timeout=5)
                if result:
                    _k, _raw = result
                    raw = _raw
                    try:
                        r.lpush(QUEUE_PROCESSING_KEY, raw)
                    except Exception:
                        pass
                    result = (_k, raw)
            _fail_streak = 0
        except Exception as e:
            _fail_streak += 1
            if _fail_streak <= 3:
                logger.debug("redis unavailable, retrying (dev: install Redis or set REDIS_URL)", err=str(e)[:160])
            else:
                logger.debug("redis still unavailable", err=str(e)[:80])
            wait = min(5 * (2 ** min(_fail_streak - 1, 3)), 30)
            _stop.wait(wait)
            continue
        if not result:
            continue
        _key, raw = result if isinstance(result, (list, tuple)) else (QUEUE_KEY, result)
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("bad payload, dropping", raw=raw[:200])
            try:
                _get_redis().lrem(QUEUE_PROCESSING_KEY, 1, raw)
            except Exception:
                pass
            continue
        ok = _process(item)
        # ack — remove from processing regardless of outcome (requeue/DLQ handles retry)
        try:
            _get_redis().lrem(QUEUE_PROCESSING_KEY, 1, raw)
        except Exception:
            pass
        if not ok:
            item["attempts"] = item.get("attempts", 0) + 1
            if item["attempts"] >= 3:
                try:
                    _get_redis().rpush(DLQ_KEY, json.dumps(item))
                    logger.error("job moved to DLQ", title=item.get("title"))
                except Exception as e:
                    logger.error("DLQ push failed", exc=e)
            else:
                try:
                    _get_redis().rpush(QUEUE_KEY, json.dumps(item))
                except Exception as e:
                    logger.error("re-enqueue failed", exc=e)

def start_worker() -> None:
    """Start the background worker thread (idempotent)."""
    global _worker_thread, _retention_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop.clear()
    _retention_thread = threading.Thread(target=_retention_loop, args=(_stop,), daemon=True, name="retention")
    _retention_thread.start()
    try:
        _get_redis().ping()
    except Exception as e:
        logger.warn("redis ping failed — task worker will retry in background (install Redis for queue)", err=str(e)[:120])
    _worker_thread = threading.Thread(target=worker_loop, daemon=True, name="task-worker")
    _worker_thread.start()
    logger.info("task worker started")

def stop_worker(timeout: float = 5.0) -> None:
    """Signal worker + retention threads to stop and wait briefly."""
    _stop.set()
    for _t in (_worker_thread, _retention_thread):
        if _t and _t.is_alive():
            try:
                _t.join(timeout=timeout / 2)
            except Exception:
                pass

