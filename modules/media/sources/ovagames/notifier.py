"""Discord webhook notifier for ovagames PC game releases."""
from __future__ import annotations

import html
import re

import os
from modules.media.common.discord import send_channel

THREAD_ENV = "OVAGAMES_DISCORD_THREAD"

_MIRROR_LABEL = re.compile(r"^www\d*\.")


def _clean_title(raw: str) -> str:
    t = html.unescape(raw or "")
    t = re.sub(r"\s*[-|]\s*(SKIDROW|TENOKE|RUNE|FLT|CODEX|DODI)\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t or raw


def _format_mirrors(mirrors: list[dict]) -> str:
    lines = []
    for m in mirrors[:15]:
        label = _MIRROR_LABEL.sub("", m.get("label", "")).strip() or "Mirror"
        url = m.get("url", "")
        if url:
            lines.append(f"• [{label}]({url})")
    return "\n".join(lines)[:1024]


def build_embed(item: dict, detail: dict | None = None) -> dict:
    title = _clean_title(item.get("title", ""))
    desc = ""
    fields = [
        {"name": "Source", "value": "Ova Games", "inline": True},
    ]

    if detail:
        syn = detail.get("synopsis") or ""
        if syn:
            desc = syn[:350] + ("..." if len(syn) > 350 else "")
        mirrors = detail.get("mirrors") or []
        dl = _format_mirrors(mirrors)
        if dl:
            fields.append({"name": f"Download ({len(mirrors)})", "value": dl, "inline": False})
        posted = detail.get("posted") or ""
        if posted:
            fields.append({"name": "Released", "value": posted, "inline": True})

    cover = (detail.get("cover") if detail else None) or ""
    return {
        "title": title,
        "url": item.get("url", ""),
        "color": 0x2E86DE,
        "thumbnail": {"url": cover} if cover else None,
        "fields": fields,
        "description": desc or None,
        "footer": {"text": "Ova Games • PC release"},
    }


def send(items: list[dict], dry_run: bool = False, details: dict | None = None) -> int:
    if not items:
        return 0
    embeds = []
    for it in items:
        detail = (details or {}).get(it["slug"])
        embeds.append(build_embed(it, detail))
    if dry_run:
        for it in items:
            print(f"[dry-run] WOULD notify: {it['title']} -> {it['url']}")
        return len(items)
    return send_channel(embeds, token_env="DISCORD_BOT_TOKEN", channel_id=os.getenv(THREAD_ENV, ""))
