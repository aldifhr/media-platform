"""Delivery jobs — consumed ONLY by the platform-discord process (token holder).

The worker's notification engine enqueues ``deliver_message``; this process
runs it and performs the actual Discord send via core.discord, then records
delivery history. No other process imports the bot token.
"""

from __future__ import annotations

from sqlalchemy import text

from core.database import get_sessionmaker
from core.discord import send_channel_message
from core.logging import get_logger
from core.queue import redis_settings

log = get_logger("discord:deliver")


async def deliver_message(
    ctx,
    *,
    event_id: str,
    channel_id: int,
    content: str = "",
    embeds: list | None = None,
    thread_id: int | None = None,
    guild_id: int | None = None,
) -> str:
    target = thread_id or channel_id
    result = await send_channel_message(str(target), content=content, embeds=embeds)
    status = "sent" if result is not None else "failed"
    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO platform.notification_deliveries
                  (event_id, guild_id, channel_id, status, error)
                VALUES (:event_id, :guild_id, :channel_id, :status, :error)
                """
            ),
            {
                "event_id": event_id,
                "guild_id": guild_id,
                "channel_id": target,
                "status": status,
                "error": None if result is not None else "send failed",
            },
        )
        await session.commit()
    if status == "failed":
        log.warn("delivery failed", event_id=event_id, channel=target)
    return status


class DiscordWorkerSettings:
    functions = [deliver_message]
    redis_settings = redis_settings()
