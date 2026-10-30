"""Source health storage + cron lock (parity with lib/services/health.ts + shared/lock.ts)."""
import time as _time

from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger

logger = get_logger("storage:health")

APP_START_TS = _time.time()

def save_source_health_map(health_map: dict) -> None:
    """Batch upsert source health rows.

    Auto-cooldown: a source with consecutive_failures >= 3 gets disabled_until
    set 30 min out (if not already disabled) so collect_recent_chapters can
    skip it instead of burning a full scrape pass every cron tick.

    Uses a direct table upsert (on_conflict=source) instead of the
    upsert_source_health_batch RPC — the RPC was silently failing to update
    existing rows (kept last_checked_at=None), leaving /status showing
    "Invalid Date" / 0ms / 0% success.
    """
    from datetime import datetime, timezone, timedelta
    from modules.manhwa.config import settings
    _COOLDOWN_MIN = 30
    rows = []
    for key, h in health_map.items():
        if key not in settings.SOURCE_KEYS:
            continue
        # Skip "disabled/cooldown" placeholders: these are written by
        # collect_recent_chapters for sources NOT scraped in this run
        # (e.g. `rss-fetch:shinigami` leaves the other in the else
        # branch with status="disabled", last_error="cooldown"). They are
        # NOT real failures — persisting them would falsely mark a healthy
        # source as down on /status. Only upsert sources that were actually
        # probed (real status healthy/degraded, no "cooldown" sentinel).
        if h.get("status") == "disabled" and h.get("last_error") == "cooldown":
            continue
        consec = h.get("consecutiveFailures", 0) or h.get("consecutive_failures", 0)
        du = h.get("disabledUntil") or h.get("disabled_until")
        now = datetime.now(timezone.utc)
        if consec >= 3:
            cooldown = (now + timedelta(minutes=_COOLDOWN_MIN)).isoformat()
            if not du or du < now.isoformat():
                du = cooldown
        _s = h.get("status", "HEALTHY")
        if _s not in ("HEALTHY", "DEGRADED", "DOWN", "BLOCKED", "RATE_LIMITED"):
            _s = _s.upper() if _s in ("healthy", "degraded") else "DEGRADED"
        rows.append({
            "source": key,
            "status": _s,
            "consecutive_failures": consec,
            "disabled_until": du,
            "last_error": h.get("lastError") or h.get("last_error"),
            "last_success_at": h.get("lastSuccessAt") or h.get("last_success_at"),
            "last_checked_at": h.get("lastCheckedAt") or h.get("last_checked_at"),
            "response_time_ms": h.get("responseTime") or h.get("response_time_ms"),
            "failures_today": h.get("failuresToday", 0) or h.get("failures_today", 0),
            "successes_today": h.get("successesToday", 0) or h.get("successes_today", 0),
        })
    if not rows:
        return
    try:
        get_supabase().table("source_health").upsert(rows, on_conflict="source").execute()
    except Exception as e:
        logger.error("saveSourceHealthMap upsert error", exc=e)

def cron_status_is_ok(raw: object) -> bool:
    """True when a cron_run_status.status value means success.

    That column is JSONB and arrives in THREE shapes depending on the client:
      * a dict            — psycopg2 with RealDictCursor
      * a JSON string     — Supabase over PostgREST: '{"status": "ok"}'
      * a bare string     — 'ok'
    Comparing the raw value to "ok" is unequal for the first two, which is how
    /incidents reported 200 "Cron Failure" entries while every row in the table
    was ok, and how /dashboard-snapshot's cronStatus.outcome always said
    "error". Single implementation so the next reader cannot pick a fourth way.
    """
    if isinstance(raw, dict):
        return raw.get("status") == "ok"
    s = str(raw or "").strip().lower()
    if not s:
        return False
    if s.startswith("{"):
        try:
            import json as _json
            return (_json.loads(s) or {}).get("status") == "ok"
        except Exception:
            return False
    return s == "ok"


def load_source_health_map(keys: list[str]) -> dict:
    try:
        res = (
            get_supabase()
            .table("source_health")
            .select("*")
            .in_("source", keys)
            .execute()
        )
        out = {}
        for r in res.data or []:
            out[r["source"]] = r
        return out
    except Exception as e:
        logger.error("loadSourceHealthMap failed", exc=e)
        return {}

def write_cron_status(status: str, chapters_sent: int = 0, matched: int = 0, duration: float | None = None) -> None:
    try:
        from datetime import datetime, timezone
        # status column is jsonb — send as dict so db_adapter serializes correctly
        _status_val = {"status": status}
        row = {
            "status": _status_val,
            "chapters_sent": chapters_sent,
            "matched": matched,
            "duration": float(duration) if duration is not None else 0.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        get_supabase().table("cron_run_status").insert(row).execute()
        return
    except Exception as e:
        logger.error("writeCronStatus failed", exc=e)

def write_dashboard_snapshot(payload: dict) -> None:
    """Persist the computed dashboard payload as a singleton row.

    Cron calls this ONCE per run (after computing the expensive
    dashboard payload). The /api/dashboard-snapshot endpoint then
    reads this 1 row (~20ms) instead of recomputing 5 parallel
    Supabase queries (~3s). Event-driven: only cron writes.

    """
    from datetime import datetime, timezone

    computed_at = datetime.now(timezone.utc).isoformat()
    # --- Redis primary (1s timeout so missing redis doesn't stall tests/cron) ---
    from modules.manhwa.core.redis import json_cache_set

    json_cache_set("dashboard_snapshot", {"payload": payload, "computed_at": computed_at}, 600)
    # --- Supabase backup (best-effort) ---
    try:
        get_supabase().table("dashboard_snapshot").upsert(
            {"id": 1, "payload": payload, "computed_at": computed_at},
            on_conflict="id",
        ).execute()
    except Exception as e:
        msg = str(e).lower()
        is_pool_closed = "already closed" in msg or "pool closed" in msg or ("connection" in msg and "closed" in msg)
        if is_pool_closed:
            logger.debug("write_dashboard_snapshot supabase skipped — pool closed", err=str(e)[:120])
        else:
            logger.error("write_dashboard_snapshot supabase backup failed", exc=e)

def read_dashboard_snapshot() -> dict | None:
    """Read the persisted singleton snapshot row, or None if absent/stale.

    TTL: a snapshot older than 5 minutes is treated as stale (cron likely
    down or failed) and returns None so the caller falls back to live DB
    queries instead of serving outdated dashboard data.

    """
    from datetime import datetime, timezone

    # --- Redis primary (skip if REDIS_URL empty for local dev without Redis) ---
    from modules.manhwa.core.redis import json_cache_get

    data = json_cache_get("dashboard_snapshot", ttl_seconds=300)
    if data is not None:
        return data

    # --- Supabase backup ---
    try:
        res = (
            get_supabase()
            .table("dashboard_snapshot")
            .select("payload, computed_at")
            .eq("id", 1)
            .limit(1)
            .maybe_single()
            .execute()
        )
        if res.data:
            computed = res.data.get("computed_at")
            if computed:
                try:
                    ct = datetime.fromisoformat(computed.replace("Z", "+00:00"))
                    if ct.tzinfo is None:
                        ct = ct.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - ct).total_seconds()
                    if age > 300:  # 5-minute TTL
                        logger.debug("dashboard_snapshot supabase stale", age_seconds=int(age))
                        return None
                except Exception:
                    return res.data  # unparseable timestamp — return data anyway
                else:
                    return res.data
            else:
                return res.data
    except Exception as e:
        logger.error("read_dashboard_snapshot supabase backup failed", exc=e)
    return None

