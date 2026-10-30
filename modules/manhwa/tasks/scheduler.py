from __future__ import annotations

import threading
import time as _time

from modules.manhwa.logger import get_logger
from modules.manhwa.core.resilience import cb_db

logger = get_logger("tasks.scheduler")

# RSS sources. Active sources = _RSS_SOURCES - disabled_sources().
_RSS_SOURCES = ("shinigami", "ikiru")

# Single parser lives in config — see config.disabled_sources() for why the
# module-level os.getenv-first pattern was dead code.
from modules.manhwa.config import disabled_sources as _disabled_sources  # noqa: E402

_SOURCE_INTERVAL_S = 300
_DISPATCH_INTERVAL_S = 120
_ENRICH_INTERVAL_S = 1200
_ENRICH_MISSING_INTERVAL_S = 1200
_ENRICH_REFRESH_INTERVAL_S = 604800
_FAILED_RETRY_INTERVAL_S = 3600
_DASHBOARD_INTERVAL_S = 600
_RETENTION_INTERVAL_S = 86400
_ALERT_INTERVAL_S = 600
# Watchdog runs on the scheduler's own beat, not as an external cron: it
# reports on dispatch lag, so it has to live where dispatch is scheduled.
_WATCHDOG_INTERVAL_S = 900
# Pipeline liveness + undelivered chapters. Every 5 min so an outage is caught
# well inside the 30-minute starvation threshold.
_DISPATCH_WATCHDOG_INTERVAL_S = 300
_VSERIES_REFRESH_INTERVAL_S = 3600

_SCHED_THREAD: threading.Thread | None = None
_stop = threading.Event()

def _scheduler_loop() -> None:
    from modules.manhwa.tasks.queue import enqueue_cron, CRON_QUEUE_KEY, CRON_PROCESSING_KEY, _get_redis

    last_enrich = 0.0
    last_enrich_missing = 0.0
    last_enrich_refresh = 0.0
    last_vseries = 0.0
    last_dispatch = 0.0
    last_failed_retry = 0.0
    last_dashboard = 0.0
    last_retention = 0.0
    last_alert = 0.0
    last_watchdog = 0.0
    last_dispatch_watchdog = 0.0
    logger.info("cron scheduler started",
                sources=_RSS_SOURCES, source_interval=_SOURCE_INTERVAL_S,
                dispatch_interval=_DISPATCH_INTERVAL_S,
                enrich_interval=_ENRICH_INTERVAL_S,
                enrich_missing_interval=_ENRICH_MISSING_INTERVAL_S,
                enrich_refresh_interval=_ENRICH_REFRESH_INTERVAL_S)
    for i, src in enumerate(_RSS_SOURCES):
        if src in _disabled_sources():
            logger.info("scheduler skip disabled source at startup", source=src)
            continue
        try:
            enqueue_cron(f"rss-fetch:{src}", source=src)
        except Exception as e:
            logger.warn("scheduler enqueue failed", src=src, err=str(e)[:120])
        if i < len(_RSS_SOURCES) - 1:
            _stop.wait(20)
    try:
        enqueue_cron("enrich", title="whitelist enrichment")
        last_enrich = _time.monotonic()
    except Exception:
        pass
    last_source = _time.monotonic()
    while True:
        try:
            # Reload each cycle so an operator toggle takes effect without a
            # restart. See config.disabled_sources().
            _disabled_set = _disabled_sources()
            if _stop.wait(_DISPATCH_INTERVAL_S):
                break
            _now = _time.monotonic()
            if _now - last_dispatch >= _DISPATCH_INTERVAL_S:
                try:
                    if "update" not in _disabled_set:
                        enqueue_cron("update", title="dispatch chapters")
                    last_dispatch = _now
                except Exception as e:
                    logger.warn("scheduler enqueue dispatch failed", err=str(e)[:120])
            if _now - last_source >= _SOURCE_INTERVAL_S:
                if not _stop.is_set():
                    for src in _RSS_SOURCES:
                        if src in _disabled_set:
                            continue
                        try:
                            logger.info("scheduler enqueue rss-fetch", source=src)
                            enqueue_cron(f"rss-fetch:{src}", source=src)
                        except Exception as e:
                            logger.warn("scheduler enqueue failed", src=src, err=str(e)[:120])
                        _stop.wait(20)
                logger.info("scheduler rss-fetch batch done", sources=_RSS_SOURCES)
                last_source = _now
            if _now - last_enrich >= _ENRICH_INTERVAL_S:
                try:
                    enqueue_cron("enrich", title="enrichment")
                    enqueue_cron("whitelist-enrich", title="whitelist enrichment")
                    last_enrich = _now
                except Exception:
                    pass
            if _now - last_enrich_missing >= _ENRICH_MISSING_INTERVAL_S:
                try:
                    enqueue_cron("enrich-missing")
                    last_enrich_missing = _now
                except Exception:
                    pass
            if _now - last_enrich_refresh >= _ENRICH_REFRESH_INTERVAL_S:
                try:
                    enqueue_cron("enrich-refresh")
                    last_enrich_refresh = _now
                except Exception:
                    pass
            if _now - last_vseries >= _VSERIES_REFRESH_INTERVAL_S:
                try:
                    enqueue_cron("vseries-refresh")
                    last_vseries = _now
                except Exception:
                    pass
            if _now - last_failed_retry >= _FAILED_RETRY_INTERVAL_S:
                try:
                    enqueue_cron("failed-retry")
                    last_failed_retry = _now
                except Exception:
                    pass
            if _now - last_alert >= _ALERT_INTERVAL_S:
                try:
                    enqueue_cron("dispatch-alert")
                    enqueue_cron("gap-detect")
                    last_alert = _now
                except Exception:
                    pass
            if _now - last_watchdog >= _WATCHDOG_INTERVAL_S:
                try:
                    enqueue_cron("watchdog")
                    last_watchdog = _now
                except Exception:
                    pass
            if _now - last_dispatch_watchdog >= _DISPATCH_WATCHDOG_INTERVAL_S:
                try:
                    enqueue_cron("dispatch-watchdog")
                    last_dispatch_watchdog = _now
                except Exception:
                    pass
            if _now - last_dashboard >= _DASHBOARD_INTERVAL_S:
                try:
                    enqueue_cron("dashboard-snapshot")
                    last_dashboard = _now
                except Exception:
                    pass
            if _now - last_retention >= _RETENTION_INTERVAL_S:
                try:
                    enqueue_cron("retention")
                    last_retention = _now
                except Exception:
                    pass
            try:
                from modules.manhwa.metrics_prometheus import REDIS_QUEUE_DEPTH
                qlen = _get_redis().llen(CRON_QUEUE_KEY)
                REDIS_QUEUE_DEPTH.labels(queue="main").set(qlen)
                REDIS_QUEUE_DEPTH.labels(queue="processing").set(_get_redis().llen(CRON_PROCESSING_KEY))
                REDIS_QUEUE_DEPTH.labels(queue="dlq").set(_get_redis().llen("beag:cron:dlq"))
                if qlen > 50:
                    logger.error("cron queue depth exceeded", queue_length=qlen, threshold=50)
            except Exception:
                pass
            # Update system metrics
            try:
                from modules.manhwa.metrics_prometheus import DB_POOL_SIZE, CIRCUIT_BREAKER_STATE, REDIS_QUEUE_DEPTH
                from modules.manhwa.db_adapter import get_pool_stats
                from modules.manhwa.core.resilience import cb_shinigami
                ps = get_pool_stats()
                DB_POOL_SIZE.labels(state="active").set(ps.get("active", 0))
                DB_POOL_SIZE.labels(state="idle").set(ps.get("idle", 0))
                for name, cb in [("db", cb_db), ("shinigami", cb_shinigami)]:
                    CIRCUIT_BREAKER_STATE.labels(service=name).set({"closed": 0, "half_open": 1, "open": 2}.get(cb.state, 0))
            except Exception:
                pass
        except Exception as e:
            logger.error("scheduler loop crashed, restarting in 30s", exc=e)
            _stop.wait(30)
            last_source = _time.monotonic()

def start_cron_scheduler() -> None:
    global _SCHED_THREAD
    if _SCHED_THREAD and _SCHED_THREAD.is_alive():
        return
    _SCHED_THREAD = threading.Thread(target=_scheduler_loop, daemon=True, name="cron-scheduler")
    _SCHED_THREAD.start()
    logger.info("cron scheduler thread started")
