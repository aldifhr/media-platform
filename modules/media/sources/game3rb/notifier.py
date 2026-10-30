"""Game3rb + ZeiGames Discord notifier (bot API, per-source thread)."""
from __future__ import annotations

import os

from modules.media.common.discord import send_channel

THREAD_ENV = "GAME3RB_DISCORD_THREAD"


def send(items: list[dict], dry_run: bool = False, details: dict | None = None,
         source: str = "game3rb", thread_id: str | None = None) -> int:
    """Send new game notifications to Discord via bot API to a thread."""
    if not items:
        return 0
    target = (thread_id or "").strip() or os.getenv(THREAD_ENV, "")
    if not target:
        print(f"[{source}] no thread configured ({THREAD_ENV}); skipping")
        return 0
    embeds = []
    for it in items:
        detail = (details or {}).get(it["slug"], {})
        embeds.append(_build_embed(it, detail, source))
    if dry_run:
        for it in items:
            print(f"[{source}][dry-run] WOULD notify: {it.get('title', it['slug'])}")
        return len(items)
    return send_channel(embeds, token_env="DISCORD_BOT_TOKEN", channel_id=target)


def _build_embed(item: dict, detail: dict, source: str) -> dict:
    title = detail.get("title") or item.get("title", item["slug"])
    synopsis = detail.get("synopsis", "")
    if len(synopsis) > 350:
        synopsis = synopsis[:347] + "..."

    fields = []
    download = detail.get("download", "")
    if download:
        fields.append({"name": "Download", "value": f"[Direct Link]({download})", "inline": True})
    posted = detail.get("posted", "")
    if posted:
        fields.append({"name": "Released", "value": posted[:100], "inline": True})

    cover = detail.get("cover", "")

    embed = {
        "title": title[:256],
        "url": item["url"],
        "color": 0x10b981 if source == "game3rb" else 0x3b82f6,
        "description": synopsis[:2048] or None,
        "footer": {"text": f"{source.title()} • PC release"},
    }

    if fields:
        embed["fields"] = fields
    if cover:
        embed["image"] = {"url": cover}
    return embed
