"""Entrypoint for the platform-discord process (``python -m apps.discord_bot``)."""

from __future__ import annotations

import uvicorn

from apps.discord_bot.interactions import create_app
from core.config import get_settings
from core.logging import get_logger

log = get_logger("discord:main")

HOST = "0.0.0.0"
PORT = 8001


def main() -> None:
    settings = get_settings()
    if not settings.discord_bot_token:
        log.error("DISCORD_BOT_TOKEN missing — this process requires it")
        raise SystemExit(2)
    if not settings.discord_public_key:
        log.error("DISCORD_PUBLIC_KEY missing — cannot verify interactions")
        raise SystemExit(2)
    log.info("starting platform-discord", host=HOST, port=PORT)
    uvicorn.run(create_app(), host=HOST, port=PORT, log_config=None)


if __name__ == "__main__":
    main()
