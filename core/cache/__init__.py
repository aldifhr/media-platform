"""Redis JSON cache (best-effort, TTL). Lifted from manhwa-scanner core/redis.py.

A missing/slow Redis never stalls a caller — every helper degrades to a no-op.
"""

from __future__ import annotations

import json
from typing import Any

import redis

from core.config import get_settings
from core.logging import get_logger

log = get_logger("core:cache")

_client: redis.Redis | None = None


def redis_client() -> redis.Redis | None:
    global _client
    if _client is not None:
        return _client
    url = get_settings().redis_url
    if not url:
        return None
    try:
        c = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.5,
        )
        c.ping()
        _client = c
        return _client
    except Exception as e:  # noqa: BLE001
        log.debug("redis unavailable", err=str(e)[:120])
        return None


def json_cache_set(key: str, value: Any, ttl_seconds: int) -> bool:
    r = redis_client()
    if r is None:
        return False
    try:
        r.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("cache set failed", key=key, err=str(e)[:120])
        return False


def json_cache_get(key: str) -> Any | None:
    r = redis_client()
    if r is None:
        return None
    try:
        raw = r.get(key)
        return json.loads(raw) if raw is not None else None
    except Exception as e:  # noqa: BLE001
        log.debug("cache get failed", key=key, err=str(e)[:120])
        return None
