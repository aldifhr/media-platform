"""platform-worker entrypoint (``python -m apps.worker``).

Notification engine loop: load subscriptions + routes from the platform schema,
run NotificationEngine.process_batch (outbox -> routing -> enqueue deliver).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from core.config import get_settings
from core.database import get_sessionmaker
from core.logging import get_logger
from core.notifications import NotificationEngine

log = get_logger("worker:engine")


async def _load_subscriptions(session) -> list[dict]:
    rows = (
        await session.execute(
            text(
                "SELECT guild_id, channel_id, module, category, enabled "
                "FROM platform.subscriptions"
            )
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def _load_routes(session) -> list[dict]:
    rows = (
        await session.execute(
            text(
                "SELECT module, event_type, rule, priority, thread_id, enabled "
                "FROM platform.notification_routes"
            )
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def run_once() -> int:
    engine = NotificationEngine()
    sm = get_sessionmaker()
    async with sm() as session:
        subs = await _load_subscriptions(session)
        routes = await _load_routes(session)
        return await engine.process_batch(session, subs, routes)


async def main() -> None:
    interval = max(1, int(get_settings().pahe_check_interval_minutes and 5))
    log.info("platform-worker engine starting", interval_s=interval)
    while True:
        try:
            n = await run_once()
            if n:
                log.info("engine enqueued deliveries", count=n)
        except Exception as e:  # noqa: BLE001
            log.error("engine loop error", err=str(e)[:200])
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
