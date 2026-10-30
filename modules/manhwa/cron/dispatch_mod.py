"""Discord dispatch + channel loading + failed-retry.

Heavy logic lives in app/services/dispatch/:
  * `channels.py` — guild/feed channel config and dead-channel tracking
  * `guards.py`   — chapter ceilings and implausible-chapter guard
  * `runner.py`   — the send pass (dispatch())

This module stays as the compatibility facade: existing call sites and tests
import from `app.cron.dispatch_mod`, and the channel-routing helpers below are
kept here because tests monkeypatch them by module attribute.

DB audit fix 4: dispatch_history has UNIQUE (title_key, source,
chapter_title) as dispatch_history_uq (042_db_audit_fix.sql) — race
guard for concurrent runners (different URLs, same chapter). dispatch_claims
is transient (FOR UPDATE SKIP LOCKED in app/services/claim.py); could be
UNLOGGED/dropped, here kept as short-TTL queue only. Duplicate
dispatch_history inserts (unique violation) are treated as already-sent.
"""

from __future__ import annotations

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger
from modules.manhwa.services.dispatch.channels import (  # noqa: F401
    _DEAD_CHANNEL_MAX_FAILS,
    _DEAD_CHANNEL_TTL,
    _DEAD_CHANNELS,
    _guild_name,
    _load_channels,
    _prune_dead_channels,
    _set_feed_channel,
)
from modules.manhwa.services.dispatch.guards import (  # noqa: F401
    is_implausible_chapter,
    load_chapter_ceilings,
)
from modules.manhwa.services.dispatch.runner import dispatch  # noqa: F401
from modules.manhwa.services.fcfs import (  # noqa: F401
    claimed_fcfs_keys as _claimed_titles,
    fcfs_key,
    normalize_chapter as _norm_chapter,
    normalize_title,
)

logger = get_logger("cron:dispatch")

__all__ = [
    "_DEAD_CHANNELS",
    "_DEAD_CHANNEL_MAX_FAILS",
    "_DEAD_CHANNEL_TTL",
    "_claimed_titles",
    "_guild_name",
    "_load_channels",
    "_norm_chapter",
    "_prune_dead_channels",
    "_set_feed_channel",
    "dispatch",
    "fcfs_key",
    "guild_setting",
    "is_implausible_chapter",
    "load_chapter_ceilings",
    "load_guild_settings",
    "logger",
    "normalize_title",
    "raw_channel_id",
    "settings",
    "split_dispatch_targets",
    "whitelist_channel_id",
]


def raw_channel_id() -> str:
    """The channel every NEW chapter is announced on.

    `RAW_DISCORD_CHANNEL_ID` in the deploy env; per-guild override wins (see
    guild_setting()). Empty everywhere ⇒ raw announcements are off and only
    whitelisted series notify, which is the previous behaviour.
    """
    return str(getattr(settings, "RAW_DISCORD_CHANNEL_ID", "") or "").strip()


def whitelist_channel_id() -> str:
    """The channel whitelisted series are announced on.

    `WHITELIST_DISCORD_CHANNEL_ID` in the deploy env. When it is unset the
    whitelist feed is off rather than aliased to the raw channel, so enabling
    raw cannot silently start notifying a channel that never asked for it.
    """
    return str(getattr(settings, "WHITELIST_DISCORD_CHANNEL_ID", "") or "").strip()


def guild_setting(guild_id: str, feed: str) -> str:
    """The configured channel for one feed on one guild, env fallback included.

    Returns "" when neither the guild row nor the env has a channel for that
    feed. The env is the fallback so a single-server deploy needs no DB row,
    while a guild that set its own channel is not overridden by a later env
    change.
    """
    try:
        from modules.manhwa.db import q

        col = "raw_channel_id" if feed == "raw" else "whitelist_channel_id"
        rows = q(f"SELECT {col} FROM guild_settings WHERE guild_id = %s", [str(guild_id)])
        val = str((rows[0].get(col) if rows else "") or "").strip()
        if val:
            return val
    except Exception:
        pass
    return raw_channel_id() if feed == "raw" else whitelist_channel_id()


def split_dispatch_targets(
    channel_ids: list[str],
) -> tuple[list[str], list[str]]:
    """Partition a channel list into (raw, whitelist).

    Dispatch used to send every chapter to every channel in `guild_settings`.
    With a raw feed that is wrong: the raw channel would carry the
    "Add to whitelist" button AND the whitelist's own notifications, so a
    series would appear on both feeds the moment it was subscribed.

    A channel is raw when it is the configured raw channel for its guild
    (guild_setting(guild, "raw"), env fallback included). Everything else —
    the configured whitelist channel, the legacy channel_id, or a channel with
    no configuration at all — stays on the whitelist half, which is what it
    received before the split.

    A channel the env names as raw is also raw even with no guild row: that is
    the single-server case, and it must not depend on a guild_settings row
    existing.
    """
    raw: list[str] = []
    wl: list[str] = []
    guilds = load_guild_settings()
    for ch in channel_ids:
        c = str(ch).strip()
        if not c:
            continue
        # A raw destination must never be included in retry's whitelist list.
        # The previous implementation put every channel in wl as well as raw;
        # retry_failed_dispatches() then re-sent a failed subscribed chapter to
        # the RAW channel. Route each channel to exactly one feed instead.
        is_raw = c == raw_channel_id()
        for g in guilds:
            if str(g.get("guild_id") or "") and c == guild_setting(str(g["guild_id"]), "raw"):
                is_raw = True
                break
        target = raw if is_raw else wl
        if c not in target:
            target.append(c)
    return raw, wl


def load_guild_settings() -> list[dict]:
    """Full per-guild rows: channel_id, label."""
    try:
        from modules.manhwa.db import get_supabase

        res = get_supabase().table("guild_settings").select("guild_id, channel_id, label").execute()
        rows = [r for r in (res.data or []) if r.get("channel_id")]
        # normalize missing optional cols
        for r in rows:
            r.setdefault("label", "")
        return rows
    except Exception as e:
        logger.error("load_guild_settings failed", err=str(e)[:120])
        return []
