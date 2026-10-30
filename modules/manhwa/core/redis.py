"""Shared Redis client + JSON cache helpers.

The ``redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.2,
socket_timeout=0.2)`` construction and the "json-encode / setex" + "get /
json-decode / staleness check" pair were re-implemented in series_meta and
health. Centralized here so the timeout/decode policy is set once.
"""
from __future__ import annotations

import json

from modules.manhwa.core.time import is_older_than
from modules.manhwa.logger import get_logger

logger = get_logger("core:redis")

# Short timeouts so a missing/slow Redis never stalls a request or cron tick.
_CONNECT_TIMEOUT = 0.2
_SOCKET_TIMEOUT = 0.2


def redis_client(redis_url: str | None = None):
    """Return a decoded Redis client, or None when unconfigured/unavailable."""
    try:
        from modules.manhwa.config import settings

        url = redis_url if redis_url is not None else getattr(settings, "REDIS_URL", "")
        if not url:
            return None
        import redis  # type: ignore

        return redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=_CONNECT_TIMEOUT,
            socket_timeout=_SOCKET_TIMEOUT,
        )
    except Exception as e:
        logger.debug("redis unavailable, fallback to in-mem/db", err=str(e)[:120])
        return None


def json_cache_set(key: str, value, ttl_seconds: int, redis_url: str | None = None) -> bool:
    """JSON-encode ``value`` into Redis under ``key`` with a TTL. Best-effort."""
    r = redis_client(redis_url)
    if not r:
        return False
    try:
        r.setex(key, ttl_seconds, json.dumps(value))
        return True
    except Exception as e:
        logger.debug("redis set failed", key=key, err=str(e)[:120])
        return False


def json_cache_get(key: str, ttl_seconds: int | None = None, redis_url: str | None = None):
    """Read + JSON-decode ``key``; return None when missing or stale.

    When ``ttl_seconds`` is given and the payload carries a ``computed_at`` /
    ``updated_at`` ISO timestamp, the value is treated as stale (None) once
    older than the TTL.
    """
    r = redis_client(redis_url)
    if not r:
        return None
    try:
        raw = r.get(key)
        if not raw:
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        logger.debug("redis get failed", key=key, err=str(e)[:120])
        return None
    if ttl_seconds is not None and isinstance(data, dict):
        ts = data.get("computed_at") or data.get("updated_at")
        if ts is not None and is_older_than(ts, ttl_seconds):
            return None
    return data
