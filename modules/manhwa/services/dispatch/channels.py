"""Discord channel configuration and dead-channel tracking.

Extracted from app/cron/dispatch_mod.py. dispatch_mod re-exports these symbols
for backward compatibility with existing call sites and tests.
"""

from __future__ import annotations

import json as _json
import os
import time
import urllib.request

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger

logger = get_logger("cron:dispatch")

# Channels that keep failing are skipped instead of retried forever. The legacy
# `channel_id` column holds a deleted channel (Discord 404) for this deploy, so
# every run recorded it as a burst failure, the retry pass then re-sent the
# chapter to the whitelist channel — a duplicate message with a degraded embed.
# TTL so a genuinely transient outage does not stay blocked for the process life.
_DEAD_CHANNELS: dict[str, float] = {}
_DEAD_CHANNEL_TTL = 6 * 3600.0
_DEAD_CHANNEL_MAX_FAILS = 3


def source_whitelist_map() -> dict[str, str]:
    """Per-source whitelist channel overrides: {channel_id: source}.

    Lets each source's whitelisted series announce on its own thread instead
    of both sharing one whitelist channel. Read from
    SHINIGAMI_WHITELIST_CHANNEL / IKIRU_WHITELIST_CHANNEL.
    """
    out: dict[str, str] = {}
    for src in ("shinigami", "ikiru"):
        ch = str(os.environ.get(f"{src.upper()}_WHITELIST_CHANNEL", "") or "").strip()
        if ch:
            out[ch] = src
    return out


def _set_feed_channel(guild_id: str, channel_id: str, feed: str) -> None:
    """Persist one feed's channel for a guild into guild_settings.

    `feed` is "raw" or "whitelist". Written as its own column rather than
    derived from channel_id, because the two feeds are identified by the
    channel they point at and a channel can be pointed at twice.
    """
    from modules.manhwa.db import q

    col = "raw_channel_id" if feed == "raw" else "whitelist_channel_id"
    # The row may not exist yet — upsert on guild_id.
    q(
        f"""INSERT INTO guild_settings (guild_id, {col})
            VALUES (%s, %s)
            ON CONFLICT (guild_id) DO UPDATE SET {col} = EXCLUDED.{col}""",
        [guild_id, channel_id],
    )


def _prune_dead_channels(channels: list[str]) -> list[str]:
    """Drop channels whose failure block has not expired."""
    now = time.monotonic()
    out: list[str] = []
    for c in channels:
        c = str(c or "").strip()
        if not c:
            continue
        blocked_at = _DEAD_CHANNELS.get(c)
        if blocked_at is not None and now - blocked_at < _DEAD_CHANNEL_TTL:
            continue
        if blocked_at is not None:
            _DEAD_CHANNELS.pop(c, None)
        if c not in out:
            out.append(c)
    return out


def _load_channels() -> list[str]:
    """Every channel dispatch should consider, one guild's feeds included.

    Used to return only `channel_id` with `.limit(1)` — the legacy single feed.
    With two feeds that silently drops the raw channel: a guild that ran
    /setrawchannel had a raw_channel_id row, and dispatch never saw it, so the
    raw feed never sent anything. The raw channel is what carries the
    "Add to whitelist" button, so this was the whole feature missing.

    Returns the de-duplicated union of channel_id, raw_channel_id,
    whitelist_channel_id, exclude_channel_id, and env RAW_DISCORD_CHANNEL_ID /
    WHITELIST_DISCORD_CHANNEL_ID / EXCLUDE_DISCORD_CHANNEL_ID.
    `channel_id` is ignored when either feed is set, so a guild that ran
    /setwhitelistchannel does not get double-notified on the legacy channel.
    """
    out: list[str] = []
    # Env channels: single-server deploy that never ran /setrawchannel.
    for _env_ch in (
        str(getattr(settings, "RAW_DISCORD_CHANNEL_ID", "") or "").strip(),
        str(getattr(settings, "WHITELIST_DISCORD_CHANNEL_ID", "") or "").strip(),
        str(getattr(settings, "EXCLUDE_DISCORD_CHANNEL_ID", "") or "").strip(),
    ):
        if _env_ch and _env_ch not in out:
            out.append(_env_ch)
    try:
        from modules.manhwa.db import get_supabase

        res = (
            get_supabase()
            .table("guild_settings")
            .select("channel_id, raw_channel_id, whitelist_channel_id, exclude_channel_id")
            .execute()
        )
        for r in res.data or []:
            _raw = str(r.get("raw_channel_id") or "").strip()
            _wl = str(r.get("whitelist_channel_id") or "").strip()
            _ex = str(r.get("exclude_channel_id") or "").strip()
            _legacy = str(r.get("channel_id") or "").strip()
            # Legacy channel_id is a fallback only when all feeds are unset.
            if _raw or _wl or _ex:
                if _raw and _raw not in out:
                    out.append(_raw)
                if _wl and _wl not in out:
                    out.append(_wl)
                if _ex and _ex not in out:
                    out.append(_ex)
            elif _legacy and _legacy not in out:
                out.append(_legacy)
        # Per-source whitelist channels (each source gets its own thread).
        for _ch in source_whitelist_map():
            if _ch not in out:
                out.append(_ch)
        return _prune_dead_channels(out)
    except Exception:
        return _prune_dead_channels(out)


_GUILD_NAME_CACHE: dict[str, tuple[float, str]] = {}
_GUILD_NAME_TTL = 3600  # 1h


def _guild_name(guild_id: str) -> str:
    """Fetch guild name via bot token (Discord API), 1h cache."""
    cached = _GUILD_NAME_CACHE.get(guild_id)
    if cached and (time.monotonic() - cached[0]) < _GUILD_NAME_TTL:
        return cached[1]
    token = getattr(settings, "DISCORD_BOT_TOKEN", "") or ""
    if not token:
        return ""
    req = urllib.request.Request(
        f"https://discord.com/api/v10/guilds/{guild_id}",
        headers={"Authorization": f"Bot {token}", "User-Agent": "manhwa-backend/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
            name = str(data.get("name") or "")
            _GUILD_NAME_CACHE[guild_id] = (time.monotonic(), name)
            return name
    except Exception:
        return ""
