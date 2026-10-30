"""Delivery-only arq worker (platform-discord).

Consumes ``deliver_message`` and sends via the unified bot (DISCORD_BOT_TOKEN).
No interactions HTTP endpoint here — manhwa's platform-api owns
/api/v1/interactive. This process is the ONLY delivery path for the unified bot.
"""

from __future__ import annotations

import asyncio

from arq import Worker

from apps.discord_bot.jobs import DiscordWorkerSettings
from core.logging import get_logger

log = get_logger("discord:delivery-worker")


async def main() -> None:
    worker = Worker(
        functions=DiscordWorkerSettings.functions,
        redis_settings=DiscordWorkerSettings.redis_settings,
    )
    log.info("delivery worker starting (unified bot)")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
