"""Discord notification for pahe posts.

Delivers through the bot API (``POST /channels/{id}/messages``) rather than a
webhook: webhooks are destroyed when the channel is recreated and then 404
forever, while a bot token + channel id survives that.

Two Discord limits used to be unguarded and turned a long title or a long
genre list into a 400 for the whole batch — the embed ``title`` (256) and each
field ``value`` (1024). Everything is now clamped before it is sent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from email.utils import parsedate_to_datetime

from modules.media.common.discord import asend_channel
from modules.media.common.env import env_str, load_env
from modules.media.common.log import get_logger

load_env()

try:
    from modules.media.sources.pahe.scanner.translate import atranslate_to_id
except ImportError:  # imported as `notify` with pahe/scanner on sys.path
    from translate import atranslate_to_id

log = get_logger("notify")

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Discord hard limits.
_TITLE_MAX = 256
_FIELD_VALUE_MAX = 1024
_DESCRIPTION_MAX = 500
_RESOLUTIONS = ("2160p", "1080p", "720p", "480p")

# --- Thread routing --------------------------------------------------------
# Discord models a thread as a channel, so posting to a thread id is the same
# API call as posting to a channel. Each rule is (env var holding the thread
# id, predicate). First match wins; no match falls through to
# PAHE_DISCORD_CHANNEL. An unset thread id disables its rule, so a rule can be
# turned off without touching code.
#
# To go threads-only, empty PAHE_DISCORD_CHANNEL. A post that then matches no
# rule has nowhere to go and is DROPPED — but loudly, one WARNING per post
# naming the title, because a silent drop is how a notifier dies quietly.
#
# Ordered by recency: a 2026 film is a current release first and an older-year
# film second. Swap the entries to invert that.
_THREAD_RULES: list[tuple[str, Callable[[Any], bool]]] = [
    ("PAHE_THREAD_2026", lambda post: post.year is not None and post.year >= 2026),
    ("PAHE_THREAD_2025", lambda post: post.year == 2025),
]


def target_channel(post) -> str:
    """Thread id for this post, or "" to use the default channel."""
    for env_var, matches in _THREAD_RULES:
        try:
            hit = matches(post)
        except Exception:  # a malformed post must not break delivery
            hit = False
        if hit:
            thread_id = env_str(env_var)
            if thread_id:
                return thread_id
            log.warning("%s matched but %s is not set; using default channel", env_var, env_var)
    return ""


def default_channel() -> str:
    """The flat-channel fallback. Empty means threads-only."""
    return env_str("PAHE_DISCORD_CHANNEL")


# Bulk delivery used to be strictly sequential with a 1s sleep after every
# post, including the last — 16 posts cost 16s of pure sleeping. A small
# semaphore keeps us inside Discord's per-channel rate limit while cutting the
# wall time to a couple of rounds.
_BULK_CONCURRENCY = 4
_BULK_DELAY_S = 0.5


def _clamp(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _format_date(date_str: str) -> str:
    """Format an RSS pubDate for the embed.

    RSS feeds emit RFC822 dates ("Sun, 12 Jul 2026 10:00:44 +0000"), NOT
    ISO 8601. datetime.fromisoformat() can't parse those, which left the
    raw string in the embed. We parse RFC822 first (email.utils), fall back
    to ISO 8601, and normalize to UTC for display.
    """
    if not date_str:
        return "N/A"
    dt = None
    try:
        dt = parsedate_to_datetime(date_str)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return date_str
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return f"{dt.day} {_MONTHS[dt.month - 1]} {dt.year} {dt.hour:02d}:{dt.minute:02d} UTC"


def _qualities(post) -> str:
    if post.qualities:
        return " / ".join(dict.fromkeys(post.qualities))
    found: list[str] = []
    for download in post.downloads:
        label = download["quality"].split("•")[-1].strip()
        base = next((r for r in _RESOLUTIONS if label.startswith(r)), "")
        if base and base not in found:
            found.append(base)
    return " / ".join(found) if found else "N/A"


def _token_configured() -> bool:
    """Only the bot token is required up front.

    The destination is resolved later — a thread id, or the flat channel — so
    requiring the channel here would mask the "no destination at all" case
    behind a generic "not configured" message.
    """
    return bool(env_str("PAHE_DISCORD_TOKEN"))


async def send_notification(post) -> bool:
    if not _token_configured():
        log.warning("PAHE_DISCORD_TOKEN not set; cannot deliver anything")
        return False

    genres = ", ".join(post.genres) if post.genres else "N/A"
    qualities = _qualities(post)

    synopsis = post.synopsis or post.description or ""
    if synopsis:
        synopsis = (await atranslate_to_id(synopsis)) or synopsis

    # No download links in the embed. pahe.ink only exposes
    # intercelestial.com/?ht=<base64> shortener URLs — 20 of them turn the
    # embed into an unreadable wall of base64 and each field value sits right
    # at Discord's 1024-char cap. The embed title links to the post page,
    # which is where the links actually belong.
    fields = [
        {"name": "🎭 Genre", "value": _clamp(genres, _FIELD_VALUE_MAX), "inline": True},
        {"name": "💿 Quality", "value": _clamp(qualities, _FIELD_VALUE_MAX), "inline": True},
        {
            "name": "📅 Date",
            "value": _clamp(_format_date(post.pub_date), _FIELD_VALUE_MAX),
            "inline": True,
        },
    ]

    embed = {
        "title": _clamp(post.title, _TITLE_MAX),
        "url": post.url,
        "color": 0x00FF00,
        "fields": fields,
        "footer": {"text": "Pahe.ink Notifier • all-scan.aldifhr.my.id"},
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if synopsis:
        embed["description"] = f"*{_clamp(synopsis, _DESCRIPTION_MAX)}*"
    if post.image:
        embed["thumbnail"] = {"url": post.image}

    thread_id = target_channel(post)
    if not thread_id and not default_channel():
        # Threads-only mode and this post matched no rule. Do not send it
        # anywhere, and do not do it quietly.
        log.warning(
            "DROPPED (no thread rule matched, PAHE_DISCORD_CHANNEL is empty) "
            "id=%s year=%s genres=%s title=%s",
            post.id,
            post.year,
            ",".join(getattr(post, "genres", []) or []),
            post.title,
        )
        return False

    sent = await asend_channel(
        [embed],
        token_env="PAHE_DISCORD_TOKEN",
        channel_env="PAHE_DISCORD_CHANNEL",
        content=post.title,
        channel_id=thread_id,
    )
    if not sent:
        log.warning(
            "notification not delivered id=%s title=%s target=%s",
            post.id,
            post.title,
            thread_id or "default-channel",
        )
    return sent > 0


async def send_bulk_notifications(posts: list) -> dict:
    """Notify every post, bounded-concurrency, and report per-post outcomes.

    A post that raises is recorded as failed rather than aborting the batch —
    one bad post must not cost us the other fifteen.
    """
    if not posts:
        return {"success": 0, "failed": 0, "results": []}

    semaphore = asyncio.Semaphore(_BULK_CONCURRENCY)

    async def deliver(post):
        async with semaphore:
            try:
                return {"id": post.id, "ok": await send_notification(post)}
            except Exception as exc:
                log.warning("notify raised id=%s err=%s: %s", post.id, type(exc).__name__, exc)
                return {"id": post.id, "ok": False}

    results = await asyncio.gather(*[deliver(post) for post in posts])
    if len(posts) > 1:
        await asyncio.sleep(_BULK_DELAY_S)
    return {
        "success": sum(r["ok"] for r in results),
        "failed": sum(not r["ok"] for r in results),
        "results": list(results),
    }
