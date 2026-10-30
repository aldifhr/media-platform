"""Shared time helpers — ISO parsing and rolling-window cutoffs.

Single source of truth for two patterns that were re-typed across storage,
services and cron:

  * ``datetime.fromisoformat(str(ts).replace("Z", "+00:00"))`` with a
    tz-aware normalization step.
  * ``datetime.now(timezone.utc) - timedelta(hours=N)`` cutoffs, in both
    ``datetime`` and ``.isoformat()`` flavours.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Default rolling window used by recent_chapters retention and the RSS feed.
RECENT_WINDOW_HOURS = 24


def parse_iso_utc(value) -> datetime | None:
    """Parse an ISO-8601 timestamp into a tz-aware UTC datetime.

    Accepts the ``Z`` suffix and naive timestamps (assumed UTC). Returns
    ``None`` for ``None``/empty/unparseable input rather than raising, so
    callers can treat a bad timestamp as "absent".
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_seconds(value) -> float | None:
    """Seconds elapsed since an ISO timestamp (UTC), or None if unparseable."""
    dt = parse_iso_utc(value)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()


def is_older_than(value, ttl_seconds: float) -> bool:
    """True when the ISO timestamp is older than ``ttl_seconds``.

    Unparseable/absent timestamps count as stale (older), matching the
    fail-open behaviour the hand-rolled checks used.
    """
    age = age_seconds(value)
    return age is None or age > ttl_seconds


def cutoff_dt(hours: int = RECENT_WINDOW_HOURS) -> datetime:
    """Tz-aware UTC datetime ``hours`` in the past."""
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def cutoff_iso(hours: int = RECENT_WINDOW_HOURS) -> str:
    """ISO-8601 string for ``hours`` in the past (UTC)."""
    return cutoff_dt(hours).isoformat()
