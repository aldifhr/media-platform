"""online-fix.me Discord notifier (bot API, per-source thread)."""
from __future__ import annotations

import os

from modules.media.common.discord import send_channel

THREAD_ENV = "ONLINEFIX_DISCORD_THREAD"


def build_embed(item: dict) -> dict:
    title = (item.get("en_title") or item.get("title", ""))[:256]
    desc = ""
    fields = [{"name": "Source", "value": "Online-Fix", "inline": True}]
    syn = item.get("en_desc") or ""
    if syn:
        desc = syn[:350] + ("..." if len(syn) > 350 else "")
    posted = (item.get("date") or "")[:10]
    if posted:
        fields.append({"name": "Released", "value": posted, "inline": True})
    cover = item.get("cover") or ""
    return {
        "title": title,
        "url": item.get("url", ""),
        "color": 0xf59e0b,
        "thumbnail": {"url": cover} if cover else None,
        "fields": fields,
        "description": desc or None,
        "footer": {"text": "Online-Fix • PC release"},
    }


def send(items: list[dict], dry_run: bool = False) -> int:
    if not items:
        return 0
    embeds = [build_embed(it) for it in items]
    if dry_run:
        for it in items:
            print(f"[online-fix][dry-run] WOULD notify: {it.get('en_title') or it.get('title')}")
        return len(items)
    thread = os.getenv(THREAD_ENV, "")
    if not thread:
        print("[online-fix] no thread configured (ONLINEFIX_DISCORD_THREAD); skipping")
        return 0
    return send_channel(embeds, token_env="DISCORD_BOT_TOKEN", channel_id=thread)
