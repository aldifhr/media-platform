"""ZeiGames Discord notifier — shares game3rb embed logic, own thread."""
from __future__ import annotations

import os

from modules.media.sources.game3rb.notifier import send as _send

THREAD_ENV = "ZEIGAMES_DISCORD_THREAD"


def send(items: list[dict], dry_run: bool = False, details: dict | None = None) -> int:
    return _send(items, dry_run=dry_run, details=details, source="zeigames",
                 thread_id=os.getenv(THREAD_ENV, ""))
