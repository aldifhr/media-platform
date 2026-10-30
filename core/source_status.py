"""Record a source check's outcome into the platform status tables.

Both the media and the manhwa scheduler call this after every check, so the
ops dashboard shows a real "last success" timestamp and a health row per
source instead of "never" for every one of them.

Best-effort by design: a status write must never break the scrape loop, so
every failure is logged and swallowed.
"""
from __future__ import annotations

import datetime as dt

from core.database import get_sessionmaker
from core.logging import get_logger

log = get_logger("core:source_status")

_UPSERT_HEALTH = """
INSERT INTO public.source_health
    (source, status, consecutive_failures, last_error, last_success_at,
     last_checked_at, response_time_ms, successes_today, failures_today,
     updated_at, created_at)
VALUES (:source, :status, 0, :error, :last_success, now(), :ms, 0, 0, now(), now())
ON CONFLICT (source) DO UPDATE SET
    status = EXCLUDED.status,
    consecutive_failures = CASE WHEN :ok THEN 0
                                ELSE public.source_health.consecutive_failures + 1 END,
    last_error = EXCLUDED.last_error,
    last_success_at = CASE WHEN :ok THEN now()
                           ELSE public.source_health.last_success_at END,
    last_checked_at = now(),
    response_time_ms = EXCLUDED.response_time_ms,
    successes_today = public.source_health.successes_today
                      + CASE WHEN :ok THEN 1 ELSE 0 END,
    failures_today = public.source_health.failures_today
                     + CASE WHEN :ok THEN 0 ELSE 1 END,
    updated_at = now()
"""


async def record_source_check(
    source: str,
    ok: bool,
    elapsed_ms: int,
    error: str = "",
) -> None:
    """Write one check's outcome to source_registry + source_health."""
    from sqlalchemy import text

    now = dt.datetime.now(dt.UTC)
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            if ok:
                await s.execute(
                    text(
                        "UPDATE platform.source_registry SET last_success_at = :ts, "
                        "error_streak = 0, updated_at = now() WHERE id = :id"
                    ),
                    {"ts": now, "id": source},
                )
            else:
                await s.execute(
                    text(
                        "UPDATE platform.source_registry SET "
                        "error_streak = error_streak + 1, updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": source},
                )
            await s.execute(
                text(_UPSERT_HEALTH),
                {
                    "source": source,
                    "status": "HEALTHY" if ok else "FAILING",
                    "error": (error or "")[:500] or None,
                    "last_success": now if ok else None,
                    "ms": int(elapsed_ms),
                    "ok": ok,
                },
            )
            await s.commit()
    except Exception as e:
        log.warning("source status write failed", source=source, err=str(e)[:160])
