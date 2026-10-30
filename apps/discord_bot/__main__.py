"""Entrypoint for the platform-discord process (``python -m apps.discord_bot``)."""

from __future__ import annotations

import asyncio
import threading

import uvicorn
from arq import Worker

from apps.discord_bot.interactions import create_app
from apps.discord_bot.jobs import DiscordWorkerSettings
from core.config import get_settings
from core.logging import get_logger

log = get_logger("discord:main")

HOST = "0.0.0.0"
PORT = 8001


def _start_delivery_worker() -> None:
    """Run the arq delivery worker (deliver_* jobs) in a daemon thread.

    This process holds the bot token, so delivery must happen here — never in
    the worker/scraper processes. A down Redis only affects this thread; the
    interactions endpoint (uvicorn) keeps serving regardless.
    """
    worker = Worker(
        functions=DiscordWorkerSettings.functions,
        redis_settings=DiscordWorkerSettings.redis_settings,
    )
    try:
        asyncio.run(worker.run())
    except Exception as e:  # noqa: BLE001
        log.error("delivery worker stopped", err=str(e)[:160])


def main() -> None:
    settings = get_settings()
    if not settings.discord_bot_token:
        log.error("DISCORD_BOT_TOKEN missing — this process requires it")
        raise SystemExit(2)
    if not settings.discord_public_key:
        log.error("DISCORD_PUBLIC_KEY missing — cannot verify interactions")
        raise SystemExit(2)
    threading.Thread(target=_start_delivery_worker, daemon=True, name="deliver-worker").start()
    log.info("starting platform-discord", host=HOST, port=PORT)
    uvicorn.run(create_app(), host=HOST, port=PORT, log_config=None)


if __name__ == "__main__":
    main()
