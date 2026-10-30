from __future__ import annotations

import logging

logger = logging.getLogger("tasks.retention")

# Must cover the recent_chapters window (7d). When the ledger was pruned to 3d
# but recent_chapters kept 7d, a chapter sent 4-7 days ago vanished from
# dispatch_history, so the claim's NOT EXISTS and /undelivered both saw it as
# never-sent and re-sent it — a duplicate. Ledger retention >= serving window.
_DISPATCH_HISTORY_RETENTION_DAYS = 7
_CRON_RUN_STATUS_RETENTION_DAYS = 7
_FAILED_DISPATCHES_RETENTION_DAYS = 7
# recent_chapters holds the working set the feed, gap-detection and enrichment
# all read. The feed itself only ever asks for 24h (every reader defaults to
# hours=24), so 3 days is headroom for the backfill/gap paths rather than a
# serving requirement.
#
# This used to be declared 7 here while lifecycle.py pruned at 72h, so the
# tighter of the two always won and this constant was a lie: raising it changed
# nothing. One definition now — lifecycle imports it.
# 7 days, not 3. /dispatch/undelivered can only surface what recent_chapters
# still holds, and a 3-day retention meant a pipeline gap of 2 days made every
# missed chapter permanently unrecoverable — invisible to the very page built
# to list misses. The table is ~650 rows at 3 days, so 7 costs almost nothing.
_RECENT_CHAPTERS_RETENTION_DAYS = 7
_RETENTION_MAX_PER_SERIES = 500
_SERIES_META_RETENTION_DAYS = 14
# Audit is a compliance trail, not a log — keep it longer than the operational
# tables. It was the only table with NO policy at all, so it grew unbounded
# (2146 rows / 30 days at the time of writing). 30 days is enough to answer
# "who changed this" for anything recent, and the table has an index on
# timestamp so the prune is cheap.
_AUDIT_LOG_RETENTION_DAYS = 30
_VACUUM_INTERVAL_S = 604800  # weekly


def _retention_loop(stop_event) -> None:
    """Hourly check: prune old rows + stale whitelist (1 bulan nggak update -> hapus)."""
    _last_whitelist_prune = 0.0
    _last_vacuum = 0.0
    while not stop_event.is_set():
        try:
            from modules.manhwa.db import get_supabase as _gsb_m
            _sb = _gsb_m()
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=_DISPATCH_HISTORY_RETENTION_DAYS)).isoformat()
            _sb.table("dispatch_history").delete().lt("sent_at", cutoff).execute()
            try:
                _cron_cutoff = (datetime.now(timezone.utc) - timedelta(days=_CRON_RUN_STATUS_RETENTION_DAYS)).isoformat()
                _sb.table("cron_run_status").delete().lt("created_at", _cron_cutoff).execute()
            except Exception as e:
                logger.warning("retention: cron_run_status cleanup failed", exc_info=e)
            try:
                _failed_cutoff = (datetime.now(timezone.utc) - timedelta(days=_FAILED_DISPATCHES_RETENTION_DAYS)).isoformat()
                _sb.table("failed_dispatches").delete().in_("status", ["resolved", "permanent_failure"]).lt("updated_at", _failed_cutoff).execute()
            except Exception as e:
                logger.warning("retention: failed_dispatches cleanup failed", exc_info=e)
            # VACUUM ANALYZE weekly to prevent bloat (Supabase pooler: no autovacuum admin)
            import time as _t3
            if _t3.time() - _last_vacuum > _VACUUM_INTERVAL_S:
                try:
                    from modules.manhwa.db import get_conn, put_conn
                    # maintenance=True: statement_timeout=0 so VACUUM is not
                    # aborted by the 10s cap that protects app queries.
                    _vc = get_conn(maintenance=True)
                    try:
                        with _vc.cursor() as cur:
                            cur.execute("VACUUM ANALYZE recent_chapters")
                            cur.execute("VACUUM ANALYZE cron_run_status")
                            cur.execute("VACUUM ANALYZE series_meta")
                        _last_vacuum = _t3.time()
                        logger.info("retention: weekly VACUUM ANALYZE done")
                    except Exception as e:
                        logger.warning("retention: vacuum failed", exc_info=e)
                    finally:
                        put_conn(_vc)
                except Exception:
                    pass
            try:
                _over = (
                    _sb.table("dispatch_history")
                    .select("title_key, source")
                    .limit(200000)
                    .execute()
                )
                from collections import Counter
                _cnt = Counter((r.get("title_key"), r.get("source")) for r in (_over.data or []))
                _bad = {k: v for k, v in _cnt.items() if v > _RETENTION_MAX_PER_SERIES}
                for (tk, src), n in _bad.items():
                    _keep = (
                        _sb.table("dispatch_history")
                        .select("sent_at")
                        .eq("title_key", tk)
                        .eq("source", src)
                        .order("sent_at", desc=True)
                        .limit(_RETENTION_MAX_PER_SERIES)
                        .execute()
                    )
                    _cutoff_ts = (_keep.data or [{}])[-1].get("sent_at") if _keep.data else None
                    if _cutoff_ts:
                        _sb.table("dispatch_history").delete().eq("title_key", tk).eq("source", src).lt("sent_at", _cutoff_ts).execute()
            except Exception as e:
                logger.warning("retention: dispatch_history per-series cap failed", exc_info=e)
            try:
                _now_iso = datetime.now(timezone.utc).isoformat()
                _stale = _sb.table("dispatch_claims").delete().lt("expires_at", _now_iso).execute()
                _stale_count = len(_stale.data) if _stale.data else 0
                _null = _sb.table("dispatch_claims").delete().is_("created_at", "null").execute()
                _null_count = len(_null.data) if _null.data else 0
                if _stale_count or _null_count:
                    logger.info("retention: cleaned stale dispatch_claims", expired=_stale_count, null_created=_null_count)
            except Exception as e:
                logger.warning("retention: stale claims cleanup failed", exc_info=e)
            try:
                from modules.manhwa.storage.error_logs import delete_older_than as _err_prune
                _pruned = _err_prune(days=7)
                if _pruned:
                    logger.info("retention: pruned error_logs", deleted=_pruned, days=7)
            except Exception as e:
                logger.warning("retention: error_logs cleanup failed", exc_info=e)
            try:
                _audit_cutoff = (datetime.now(timezone.utc) - timedelta(days=_AUDIT_LOG_RETENTION_DAYS)).isoformat()
                # Column is `timestamp` per migration 064 DDL. Filtering a
                # non-existent column made Postgres error every hour and the
                # except below swallowed it, so audit_log was never pruned.
                _pruned_audit = _sb.table("audit_log").delete().lt("timestamp", _audit_cutoff).execute()
                _audit_count = len(_pruned_audit.data) if _pruned_audit.data else 0
                if _audit_count:
                    logger.info("retention: pruned audit_log", deleted=_audit_count, days=_AUDIT_LOG_RETENTION_DAYS)
            except Exception as e:
                # A retention that fails silently is the most expensive bug in
                # this file: nobody knows until the disk fills. ERROR, not warning.
                logger.error("retention: audit_log cleanup FAILED — table is growing unbounded", exc_info=e)
            try:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=_SERIES_META_RETENTION_DAYS)).isoformat()
                _wl = _sb.table("whitelist").select("title_key").limit(50000).execute()
                _wl_keys = {r.get("title_key") for r in (_wl.data or []) if r.get("title_key")}
                _rc = _sb.table("recent_chapters").select("title_key").gte("updated_time", cutoff).limit(100000).execute()
                _rc_keys = {r.get("title_key") for r in (_rc.data or []) if r.get("title_key")}
                keep = _wl_keys | _rc_keys
                q = _sb.table("series_meta").select("title_key").lt("updated_at", cutoff).limit(100000).execute()
                to_del = [r.get("title_key") for r in (q.data or []) if r.get("title_key") and r.get("title_key") not in keep]
                if to_del:
                    _sb.table("series_meta").delete().in_("title_key", to_del).execute()
                    logger.info("retention: pruned series_meta", deleted=len(to_del), days=_SERIES_META_RETENTION_DAYS)
            except Exception as e:
                logger.warning("retention: series_meta cleanup failed", exc_info=e)
            try:
                import time as _t2
                # Whitelist auto-cleanup is OPT-IN. It used to run every day
                # with days=30 and a "never notified" signal read from
                # dispatch_history, which is pruned to 3 days — so any series
                # that had not released in 3+ days was deleted. Subscriptions
                # persist until the user removes them; only run this when an
                # operator explicitly sets WHITELIST_AUTO_CLEANUP_DAYS.
                _wl_days = 0
                try:
                    from modules.manhwa.config import settings as _settings

                    _wl_days = int(getattr(_settings, "WHITELIST_AUTO_CLEANUP_DAYS", 0) or 0)
                except Exception:
                    _wl_days = 0
                if _wl_days > 0 and _t2.time() - _last_whitelist_prune > 86400:
                    from modules.manhwa.storage.whitelist import auto_cleanup_stale_whitelist

                    _r = auto_cleanup_stale_whitelist(days=_wl_days)
                    if _r.get("removed"):
                        logger.info(
                            "retention: pruned stale whitelist",
                            removed=_r.get("removed"),
                            days=_wl_days,
                        )
                    _last_whitelist_prune = _t2.time()
            except Exception as e:
                logger.warning("retention: whitelist cleanup failed", exc_info=e)
            try:
                _rc_cutoff = (datetime.now(timezone.utc) - timedelta(days=_RECENT_CHAPTERS_RETENTION_DAYS)).isoformat()
                _pruned_rc = _sb.table("recent_chapters").delete().lt("updated_time", _rc_cutoff).execute()
                _rc_count = len(_pruned_rc.data) if _pruned_rc.data else 0
                if _rc_count:
                    logger.info("retention: pruned recent_chapters", deleted=_rc_count, days=_RECENT_CHAPTERS_RETENTION_DAYS)
            except Exception as e:
                logger.warning("retention: recent_chapters cleanup failed", exc_info=e)
        except Exception:
            logger.exception("retention prune failed")
        try:
            stop_event.wait(3600)
        except (KeyboardInterrupt, SystemExit):
            break
