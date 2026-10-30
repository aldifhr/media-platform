"""Shared alert delivery for cron notifiers.

Two blocks were copy-pasted across source_alert, dispatch_watchdog, watchdog,
dispatch_alert and gap_detector:

  1. resolving the admin alert channel (ADMIN_REPORT_CHANNEL_ID, else the
     first guild_settings channel), and
  2. sending a message to Discord.

Both live here so a change to the channel fallback happens once.
"""
from __future__ import annotations

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger

logger = get_logger("cron:notify_common")


def resolve_alert_channel_id() -> str | None:
    """Explicit admin channel, else the first guild channel.

    Falls back to the chapter channel (the same one chapters dispatch to) so
    an alert lands somewhere even when ADMIN_REPORT_CHANNEL_ID is unset.
    """
    cid = (settings.ADMIN_REPORT_CHANNEL_ID or "").strip()
    if cid:
        return cid
    try:
        from modules.manhwa.db import get_supabase

        res = get_supabase().table("guild_settings").select("channel_id").limit(1).execute()
        rows = res.data or []
        if rows and rows[0].get("channel_id"):
            return str(rows[0]["channel_id"])
    except Exception as e:
        logger.warn("alert channel lookup failed", err=str(e)[:120])
    return None


def send_alert(
    content: str,
    *,
    log_label: str = "alert",
) -> bool:
    """Send ``content`` to the resolved alert channel.

    Returns True when a Discord send was attempted. Never raises — cron callers
    must not crash on a notification failure.
    """
    cid = resolve_alert_channel_id()
    if not cid:
        logger.warn("no alert channel configured — skipping alert", label=log_label)
        return False
    sent = False
    try:
        from modules.manhwa.discord import client as discord_client

        discord_client.send_channel_message(cid, content=content)
        sent = True
    except Exception as e:
        logger.warn("alert send failed", label=log_label, err=str(e)[:160])
    return sent
