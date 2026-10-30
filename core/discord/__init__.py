"""Discord transport: Ed25519 interaction verify + bot-API REST delivery.

HTTP-interactions model (ADR-001). The gateway-websocket fallback sender is
added in Phase 3 alongside the discord-bot process. Token is read only here
and in the discord-bot process — never in workers/scrapers.
"""

from __future__ import annotations

from typing import Any

import httpx
from discord_interactions import verify_key

from core.config import get_settings
from core.logging import get_logger

log = get_logger("core:discord")

_API = "https://discord.com/api/v10"


def verify_interaction(raw_body: bytes, signature: str, timestamp: str) -> bool:
    pk = get_settings().discord_public_key
    if not pk:
        return False
    try:
        return bool(verify_key(raw_body, signature, timestamp, pk))
    except Exception:  # noqa: BLE001
        return False


async def send_channel_message(
    channel_id: str,
    content: str = "",
    embeds: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Deliver via bot REST. 400/403/404 are permanent (no retry, no gateway)."""
    token = get_settings().discord_bot_token
    if not token:
        log.warn("no bot token; cannot send", channel=channel_id)
        return None
    body: dict[str, Any] = {}
    if content:
        body["content"] = content
    if embeds:
        body["embeds"] = embeds
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{_API}/channels/{channel_id}/messages",
            json=body,
            headers={"Authorization": f"Bot {token}"},
        )
    if r.status_code in (400, 403, 404):
        log.warn("discord send permanent fail", status=r.status_code, channel=channel_id)
        return None
    r.raise_for_status()
    return r.json()
