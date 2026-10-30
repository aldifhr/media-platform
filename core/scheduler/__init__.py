"""Distributed locking for the scheduler.

Guarantees a single scheduler across processes. Redis SET NX PX with an
owner-token release (atomic via Lua). Degrades to "not acquired" when Redis
is down (fail-safe: caller skips the tick rather than double-running).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from core.cache import redis_client
from core.logging import get_logger

log = get_logger("core:scheduler")

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)


@asynccontextmanager
async def redis_lock(name: str, ttl_ms: int = 30000):
    r = redis_client()
    token = uuid.uuid4().hex
    key = f"lock:{name}"
    acquired = False
    if r is not None:
        try:
            acquired = bool(r.set(key, token, nx=True, px=ttl_ms))
        except Exception as e:  # noqa: BLE001
            log.debug("lock acquire failed", key=key, err=str(e)[:120])
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        if r is not None:
            try:
                r.eval(_RELEASE_LUA, 1, key, token)
            except Exception as e:  # noqa: BLE001
                log.debug("lock release failed", key=key, err=str(e)[:120])
