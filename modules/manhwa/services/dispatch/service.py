"""Dispatch service — handles Discord notification logic.

Extracted from dispatch_mod.py for cleaner separation.
"""

from __future__ import annotations

import html

from modules.manhwa.core.resilience import (
    cb_db,
    cb_discord,
    retry_with_backoff,
    with_circuit_breaker,
)
from modules.manhwa.db import get_supabase
from modules.manhwa.discord import client as discord
from modules.manhwa.discord.embeds import build_chapter_embed
from modules.manhwa.logger import get_logger
from modules.manhwa.services.shared import fcfs_key

logger = get_logger("services:dispatch")


class DispatchService:
    """Handle chapter dispatch to Discord with FCFS dedupe."""

    @retry_with_backoff(max_retries=3, base_delay=0.3, max_delay=3.0)
    @with_circuit_breaker(cb_db)
    def get_target_channels(self) -> list[str]:
        """Load target channels from guild_settings."""
        res = get_supabase().table("guild_settings").select("channel_id").execute()
        return [r["channel_id"] for r in (res.data or []) if r.get("channel_id")]

    @retry_with_backoff(max_retries=3, base_delay=0.3, max_delay=3.0)
    @with_circuit_breaker(cb_db)
    def get_claimed_keys(self, keys: list[str]) -> set[str]:
        """Return FCFS keys already sent (permanently in dispatch_history)."""
        from modules.manhwa.storage.dispatch.history import _already_dispatched_fcfs

        return _already_dispatched_fcfs(keys)

    @retry_with_backoff(max_retries=3, base_delay=0.3, max_delay=3.0)
    @with_circuit_breaker(cb_db)
    def get_claimed_urls(self, urls: list[str]) -> set[str]:
        """Return URLs already in dispatch_history."""
        from modules.manhwa.storage.dispatch.history import _already_dispatched

        return _already_dispatched(urls)

    @retry_with_backoff(max_retries=3, base_delay=0.5, max_delay=5.0)
    @with_circuit_breaker(cb_discord)
    def send_chapter(self, item: dict, channel_id: str) -> bool:
        """Send a single chapter notification to Discord."""
        cover_url = item.get("cover", "")
        attachment = None
        attachment_filename = ""

        if cover_url and isinstance(cover_url, str) and cover_url.startswith("http"):
            from modules.manhwa.discord.http import fetch_cover

            result = fetch_cover(cover_url)
            if result:
                content_bytes, content_type = result
                # Derive extension from the ACTUAL content type, not the URL.
                # _resize_cover re-encodes everything to JPEG, so a .png URL
                # yields image/jpeg bytes — naming the file .png makes Discord
                # reject the attachment and the thumbnail renders blank.
                ext_map = {
                    "image/jpeg": "jpg",
                    "image/png": "png",
                    "image/webp": "webp",
                    "image/gif": "gif",
                }
                ext = ext_map.get(content_type, "jpg")
                attachment_filename = (
                    f"cover_{fcfs_key(item.get('title', ''), item.get('chapter', ''))[:20]}.{ext}"
                )
                attachment = (attachment_filename, content_bytes, content_type)

        embed = build_chapter_embed(
            title=item.get("title", ""),
            chapter=item.get("chapter", ""),
            url=item.get("url", ""),
            series_url=item.get("series_url", ""),
            source=item.get("source", ""),
            cover=item.get("cover", ""),
            rating=str(item.get("rating", "")),
            genres=item.get("genres", []),
            description=item.get("description", ""),
            updated_time=item.get("updated_time", ""),
        )
        if attachment:
            embed["thumbnail"] = {"url": f"attachment://{attachment_filename}"}

        _title = html.unescape(item.get("title", "Unknown"))
        content = f"🔔 New Release on **{_title}** — Chapter {item.get('chapter', '?')}"

        try:
            if attachment:
                resp = discord.send_channel_message_with_attachments(
                    channel_id=channel_id, content=content, embeds=[embed], attachments=[attachment]
                )
            else:
                resp = discord.send_channel_message(
                    channel_id=channel_id, content=content, embeds=[embed]
                )
            return resp is not None
        except Exception as e:
            logger.warn("send_chapter failed", err=str(e)[:120])
            return False

    def update_latest_sent_chapter(self, title_key: str, source: str, chapter_num: float):
        """Update latest_sent_chapter in whitelist."""
        try:
            from modules.manhwa.db import q

            q(
                "UPDATE whitelist SET latest_sent_chapter = "
                "GREATEST(COALESCE(latest_sent_chapter, 0), %s) "
                "WHERE title_key = %s AND source = %s",
                [chapter_num, title_key, source],
            )
        except Exception as e:
            logger.warn("update_latest_sent_chapter failed", err=str(e)[:120])

    def update_latest_chapter(self, title_key: str, source: str, chapter_num: float):
        """Update latest_chapter (highest chapter seen/available) in whitelist."""
        try:
            from modules.manhwa.db import q

            q(
                "UPDATE whitelist SET latest_chapter = "
                "GREATEST(COALESCE(latest_chapter, 0), %s) "
                "WHERE title_key = %s AND source = %s",
                [chapter_num, title_key, source],
            )
        except Exception as e:
            logger.warn("update_latest_chapter failed", err=str(e)[:120])

    # Facade for storage claim (storage leaky fix - dispatch owns orchestration)
    def claim_for_dispatch(
        self, whitelist: list[dict] | None = None, hours: int = 24, limit: int = 500
    ) -> list[dict]:
        """Delegate to storage but owned by dispatch service (single seam)."""
        from modules.manhwa.storage.recent_chapters import claim_recent_chapters_for_dispatch

        return claim_recent_chapters_for_dispatch(whitelist=whitelist, hours=hours, limit=limit)


# Singleton
dispatch_service = DispatchService()
