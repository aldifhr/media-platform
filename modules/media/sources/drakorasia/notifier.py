"""Discord notifier for drakorasia drama episodes (bot API, manhwa-style embed).

Each category (korea / china) is delivered to its own thread, configured via
DRAKORASIA_KOREA_DISCORD_THREAD / DRAKORASIA_CHINA_DISCORD_THREAD. A category
with no thread configured is dropped loudly rather than falling back to a
shared channel.
"""
from __future__ import annotations

import html
import os
from datetime import UTC, datetime

from modules.media.common.discord import send_channel

_LABEL = "Drakorasia"
# category -> thread env var. "korea" is also the fallback for an untagged item.
THREAD_ENV = {
    "korea": "DRAKORASIA_KOREA",
    "china": "DRAKORASIA_CHINA",
}
_FALLBACK_ENV = "DRAKORASIA_DISCORD_THREAD"


def thread_for(cat: str) -> str:
    """The Discord thread id for one category ("" when unconfigured)."""
    env = THREAD_ENV.get(cat, _FALLBACK_ENV)
    return os.getenv(env, "") or os.getenv(_FALLBACK_ENV, "")


def _fmt_published(ts: str) -> str:
    if not ts:
        return ""
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    return d.strftime("%B %d, %Y")


def build_embed(item: dict, detail: dict | None = None) -> dict:
    title = html.unescape(item.get("title", "")) or "Drama"
    fields = []
    pub = _fmt_published(item.get("published", ""))
    if pub:
        fields.append({"name": "🕐 Released", "value": pub, "inline": True})
    fields.append({"name": "🔗 Source", "value": f"`{_LABEL}`", "inline": True})
    desc = ""
    if detail:
        syn = detail.get("synopsis") or item.get("description") or ""
        if syn:
            desc = syn[:350] + ("..." if len(syn) > 350 else "")
    cover = (detail.get("cover") if detail else None) or item.get("image") or ""
    return {
        "title": title,
        "url": item.get("url", ""),
        "color": 0x8E44AD,
        "thumbnail": {"url": cover} if cover else None,
        "fields": fields,
        "description": desc or None,
        "footer": {"text": f"Source: {_LABEL}"},
        "timestamp": datetime.now(UTC).isoformat(),
    }


def send(items: list[dict], dry_run: bool = False, details: dict | None = None) -> int:
    """Send one embed batch per category thread."""
    if not items:
        return 0
    by_cat: dict[str, list[dict]] = {}
    for it in items:
        by_cat.setdefault(it.get("category") or "korea", []).append(it)

    sent = 0
    for cat, cat_items in by_cat.items():
        thread = thread_for(cat)
        if not thread:
            print(f"[drakorasia] no thread configured for {cat} — dropping {len(cat_items)}")
            continue
        if dry_run:
            for it in cat_items:
                print(f"[dry-run] WOULD notify [{cat}]: {it['title']} -> {it['url']}")
            sent += len(cat_items)
            continue
        embeds = [build_embed(it, (details or {}).get(it["slug"])) for it in cat_items]
        sent += send_channel(embeds, token_env="DISCORD_BOT_TOKEN", channel_id=thread)
    return sent
