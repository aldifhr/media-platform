import asyncio

from core import cache, scheduler
from core.config import Settings

DEAD = "redis://127.0.0.1:6390/0"


def _dead_redis(monkeypatch):
    monkeypatch.setattr(cache, "get_settings", lambda: Settings(redis_url=DEAD))


def test_cache_graceful_when_redis_down(monkeypatch):
    _dead_redis(monkeypatch)
    assert cache.json_cache_get("missing") is None
    assert cache.json_cache_set("k", {"a": 1}, 60) is False


def test_lock_not_acquired_when_redis_down(monkeypatch):
    _dead_redis(monkeypatch)

    async def run():
        async with scheduler.redis_lock("t") as acquired:
            return acquired

    assert asyncio.run(run()) is False
