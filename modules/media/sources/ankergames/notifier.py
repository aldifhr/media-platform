"""Discord notifier for AnkerGames.net multiplayer releases (bot API, manhwa-style embed)."""
from __future__ import annotations

import os
import re
from datetime import UTC, datetime

from modules.media.common.discord import send_channel

THREAD_ENV = "ANKERGAMES_DISCORD_THREAD"
_LABEL = "AnkerGames"
_MIRROR_LABEL = re.compile(r"^www\d*\.")


def _fmt_size(gb) -> str:
    if not gb:
        return ""
    if gb >= 1000:
        return f"{gb / 1000:.1f} TB"
    return f"{gb:.1f} GB"


def _fmt_files(files: list[dict]) -> str:
    lines = []
    for f in files[:10]:
        label = f.get("label", "File")
        kind = f.get("kind", "")
        if kind:
            label = f"{label} ({kind})"
        lines.append(f"• {label}")
    return "\n".join(lines)


def build_embed(item: dict, detail: dict | None = None) -> dict:
    title = item.get("title", "")
    subtitle = item.get("subtitle", "")
    edition = item.get("edition", "")
    version = item.get("version", "")
    size = _fmt_size(item.get("size_gb"))
    developer = item.get("developer", "")
    publisher = item.get("publisher", "")
    genres = item.get("genres", [])
    overview = (item.get("overview") or "")[:350]
    cover = item.get("cover") or item.get("poster") or ""
    store_url = item.get("store_url", "")
    steam_id = item.get("steam_app_id", "")
    co_op = item.get("co_op")
    online_fix = item.get("online_fix")

    # Build title line
    parts = [title]
    if subtitle and subtitle != title:
        parts.append(subtitle)
    title_line = " — ".join(parts)

    fields = []
    if size:
        fields.append({"name": "💾 Size", "value": size, "inline": True})
    if version:
        fields.append({"name": "🔖 Version", "value": version, "inline": True})
    if developer:
        fields.append({"name": "🏢 Developer", "value": developer[:50], "inline": True})
    if publisher and publisher != developer:
        fields.append({"name": "📢 Publisher", "value": publisher[:50], "inline": True})
    if co_op:
        fields.append({"name": "🤝 Co-Op", "value": "Yes", "inline": True})
    if online_fix:
        fields.append({"name": "🌐 Online Fix", "value": "Yes", "inline": True})
    if genres:
        genre_str = " · ".join(genres[:8])
        fields.append({"name": "🎮 Genres", "value": f"`{genre_str}`", "inline": False})

    # Steam link
    if steam_id:
        fields.append({"name": "🔗 Source", "value": f"[Steam]({store_url})" if store_url else f"`{_LABEL}`", "inline": True})
    else:
        fields.append({"name": "🔗 Source", "value": f"`{_LABEL}`", "inline": True})

    # Download files
    files = item.get("files") or []
    if files:
        dl = _fmt_files(files)
        fields.append({"name": f"📥 Downloads ({len(files)})", "value": dl, "inline": False})

    # Description from detail — prefer clean overview over raw repack_features
    desc = (item.get("overview") or "").strip()
    if detail:
        if not desc:
            desc = (detail.get("repack_features") or "").strip()
        # fallback: strip HTML if only repack_features available
        if desc and "<" in desc:
            import re
            desc = re.sub(r"<[^>]+>", " ", desc)
            desc = re.sub(r"\s+", " ", desc).strip()

    return {
        "title": title_line[:256],
        "url": item.get("url", ""),
        "color": 0x2ECC71,
        "thumbnail": {"url": cover} if cover else None,
        "fields": fields,
        "description": desc or None,
        "footer": {"text": f"Source: {_LABEL}"},
        "timestamp": datetime.now(UTC).isoformat(),
    }


def send(items: list[dict], dry_run: bool = False, details: dict | None = None) -> int:
    if not items:
        return 0
    embeds = []
    for it in items:
        detail = (details or {}).get(str(it["id"]))
        embeds.append(build_embed(it, detail))
    if dry_run:
        for it in items:
            print(f"[dry-run] WOULD notify: {it['title']} -> {it['url']}")
        return len(items)
    return send_channel(
        embeds,
        token_env="DISCORD_BOT_TOKEN",
        channel_id=os.getenv(THREAD_ENV, ""),
    )
