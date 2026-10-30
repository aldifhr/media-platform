"""Async job queue on Redis via arq (the ONE queue framework).

Redis stays a cache too (core.cache); arq uses it as the job broker.
"""

from __future__ import annotations

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from core.config import get_settings
from core.logging import get_logger

log = get_logger("core:queue")

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def enqueue(job_name: str, *args, **kwargs):
    pool = await get_pool()
    job = await pool.enqueue_job(job_name, *args, **kwargs)
    log.debug("enqueued", job=job_name)
    return job


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
