"""freetp.org Discord notifier (bot API, per-source thread)."""
from __future__ import annotations

import os

from modules.media.common.discord import send_channel

THREAD_ENV = "FREETP_DISCORD_THREAD"


def build_embed(item: dict) -> dict:
    title = (item.get("en_title") or item.get("title", ""))[:256]
    embed = {
        "title": title,
        "url": item.get("url", ""),
        "color": 0x8b5cf6,
        "footer": {"text": "FreeTP • network game"},
    }
    if item.get("en_desc"):
        embed["description"] = item["en_desc"][:2000]
    if item.get("cover"):
        embed["image"] = {"url": item["cover"]}
    return embed


def send(items: list[dict], dry_run: bool = False) -> int:
    if not items:
        return 0
    embeds = [build_embed(it) for it in items]
    if dry_run:
        for it in items:
            print(f"[freetp][dry-run] WOULD notify: {it.get('en_title') or it.get('title')}")
        return len(items)
    thread = os.getenv(THREAD_ENV, "")
    if not thread:
        print("[freetp] no thread configured (FREETP_DISCORD_THREAD); skipping")
        return 0
    return send_channel(embeds, token_env="DISCORD_BOT_TOKEN", channel_id=thread)
