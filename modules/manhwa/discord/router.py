"""Discord interaction router (parity with api/interactive.ts + lib/commands/*)."""
from __future__ import annotations

import json

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger

logger = get_logger("discord:router")

# InteractionType / ResponseType constants
PING = 1
APPLICATION_COMMAND = 2
MESSAGE_COMPONENT = 3
CHANNEL_MESSAGE_WITH_SOURCE = 4
DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5

# MessageFlags.EPHEMERAL — only the interacting user sees the reply. Used for
# button confirmations so repeated whitelist adds do not spam the channel.
EPHEMERAL = 1 << 6

def _extract_options(data: dict) -> dict:
    opts = {}
    for o in data.get("options", []) or []:
        if "value" in o:
            opts[o["name"]] = o.get("value")
        elif "options" in o:  # subcommand
            opts[o["name"]] = _extract_options(o)
    return opts

def _respond(response_type: int, data: dict | None = None) -> dict:
    resp = {"type": response_type}
    if data is not None:
        resp["data"] = data
    return resp

def handle_interaction(raw_body: bytes) -> tuple[int, dict]:
    """Returns (http_status, json_body)."""
    try:
        payload = json.loads(raw_body)
    except Exception:
        return 400, {"error": "invalid_json"}

    itype = payload.get("type")

    if itype == PING:
        return 200, _respond(1)  # PONG

    data = payload.get("data", {}) or {}
    name = data.get("name")
    custom_id = data.get("custom_id", "")

    # Defer + handle async (we respond with DEFERRED, then edit)
    if itype in (APPLICATION_COMMAND, MESSAGE_COMPONENT):
        # route
        if name == "add":
            return _route_add(payload, data)
        if name == "search":
            return _route_search(data)
        if name == "stats":
            return _route_stats(data)
        if name == "help":
            return 200, _respond(
                CHANNEL_MESSAGE_WITH_SOURCE,
                {"content": "Available: /add, /search, /stats, /help, /setrawchannel, /setwhitelistchannel"},
            )
        if name == "setrawchannel":
            return _route_setrawchannel(payload, data)
        if name == "setwhitelistchannel":
            return _route_setwhitelistchannel(payload, data)
        if name == "setfilter":
            return _route_setfilter(payload, data)
        if custom_id.startswith("wladd:"):
            return _route_whitelist_add(payload, data)
        if custom_id.startswith("list:"):
            return _route_list_component(custom_id)
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": f"Unknown command `/{name}`. Available: `/add`, `/search`, `/stats`, `/help`, `/setrawchannel`, `/setwhitelistchannel`, `/setfilter`"},
        )

    return 400, {"error": "unsupported_interaction"}

def _route_add(payload: dict, data: dict):
    opts = _extract_options(data)
    title = opts.get("title", "")
    url = opts.get("link", "") or opts.get("url", "")
    # queue to background (deferred)
    from modules.manhwa.tasks import enqueue_add

    enqueue_add(title=title, url=url, interaction=payload)
    return 200, _respond(
        DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE,
        {"content": f"⏳ Adding **{title}**..."},
    )

def _route_search(data: dict):
    opts = _extract_options(data)
    q = opts.get("query", "")
    from modules.manhwa.sources import shinigami

    # shinigami is the only scraper with search.
    results = shinigami.search_shinigami_api(q, 5)
    if not results:
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE, {"content": f"No results for `{q}`"}
        )
    lines = [f"🔍 **{q}**"]
    for r in results[:10]:
        t = r.get("title", "?")
        lines.append(f"• {t}")
    return 200, _respond(CHANNEL_MESSAGE_WITH_SOURCE, {"content": "\n".join(lines)})

def _route_stats(data: dict):
    from modules.manhwa.storage import whitelist

    rows = whitelist.load_whitelist()
    return 200, _respond(
        CHANNEL_MESSAGE_WITH_SOURCE,
        {"content": f"📊 Whitelist: **{len(rows)}** manga tracked"},
    )

def _route_setrawchannel(payload: dict, data: dict):
    """/setrawchannel — the feed every new chapter lands on, with the button."""
    return _route_set_feed_channel(payload, data, feed="raw")


def _route_setwhitelistchannel(payload: dict, data: dict):
    """/setwhitelistchannel — the feed only whitelisted series land on."""
    return _route_set_feed_channel(payload, data, feed="whitelist")


def _route_set_feed_channel(payload: dict, data: dict, feed: str):
    """Shared body for /setrawchannel and /setwhitelistchannel."""
    opts = _extract_options(data)
    channel_id = str(opts.get("channel", "") or "").strip()
    guild_id = str(payload.get("guild_id", "") or "").strip()
    if not channel_id or not guild_id:
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": "❌ Missing channel or guild"},
        )
    # Defense: only accept guilds in the allowlist.
    _raw = getattr(settings, "DISCORD_GUILD_ID", "") or ""
    allowed = [g.strip() for g in _raw.split(",") if g.strip()]
    if allowed and guild_id not in allowed:
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": "❌ Channel must belong to an authorized server"},
        )
    try:
        from modules.manhwa.cron.dispatch_mod import _set_feed_channel

        def _do_upsert():
            return _set_feed_channel(guild_id, channel_id, feed)

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as _ex:
            _ex.submit(_do_upsert).result(timeout=2.5)
        label = "raw (every new chapter + Add to whitelist button)" if feed == "raw" else "whitelist (whitelisted series only)"
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": f"✅ **{feed.capitalize()}** feed will use <#{channel_id}> — {label}."},
        )
    except TimeoutError:
        logger.warn("setfeed DB timeout", feed=feed, guild=guild_id)
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": "⏳ Channel save is slow — try again in 5s (DB timeout)."},
        )
    except Exception as e:
        logger.warn("setfeed failed", feed=feed, err=str(e)[:200])
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": f"❌ Failed to set the {feed} channel: {str(e)[:160]}"},
        )


def _route_setfilter(payload: dict, data: dict):
    """Per-guild type filter: /setfilter types:manhwa,manhua — empty = all."""
    opts = _extract_options(data)
    guild_id = payload.get("guild_id", "")
    types_raw = (opts.get("types") or "").lower()
    types = {t.strip() for t in types_raw.split(",") if t.strip()}
    bad = types - {"manhwa", "manga", "manhua"}
    if bad:
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": f"❌ Invalid types: {', '.join(sorted(bad))}. Use manhwa, manga, manhua (comma-separated, empty = all)."},
        )
    if not guild_id:
        return 200, _respond(CHANNEL_MESSAGE_WITH_SOURCE, {"content": "❌ Missing guild"})
    try:
        from modules.manhwa.db import get_supabase

        def _do_upsert():
            return get_supabase().table("guild_settings").upsert(
                {"guild_id": guild_id, "type_filter": ",".join(sorted(types))},
                on_conflict="guild_id",
            ).execute()

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as _ex:
            _ex.submit(_do_upsert).result(timeout=2.5)
        msg = f"✅ This server will now receive: **{', '.join(sorted(types))}**" if types else "✅ Filter cleared — this server receives ALL types"
        return 200, _respond(CHANNEL_MESSAGE_WITH_SOURCE, {"content": msg})
    except TimeoutError:
        logger.warn("setfilter DB timeout", guild=guild_id)
        return 200, _respond(CHANNEL_MESSAGE_WITH_SOURCE, {"content": "⏳ Filter save is slow — try again in 5s (DB timeout)."})
    except Exception as e:
        return 200, _respond(CHANNEL_MESSAGE_WITH_SOURCE, {"content": f"❌ Failed: {e}"})

def _wl_add_allowed(payload: dict) -> bool:
    """Role gate for the "Add to whitelist" button.

    Empty WHITELIST_ROLE_IDS keeps the old behaviour (anyone may click). A
    non-empty list is authoritative: a clicker must carry at least one of those
    role ids. Discord sends them in member.roles for component interactions in
    a guild.
    """
    from modules.manhwa.config import settings

    allowed = {r.strip() for r in str(settings.WHITELIST_ROLE_IDS or "").split(",") if r.strip()}
    if not allowed:
        return True
    roles = {str(r) for r in ((payload.get("member") or {}).get("roles") or [])}
    return bool(roles & allowed)


def _route_whitelist_add(payload: dict, data: dict):
    """"Add to whitelist" button on a RAW-feed embed.

    The button carries the series' own (source, title_key), so the click writes
    the row directly — no search, no ambiguity about which series was meant.
    The display title and series URL come from the embed the button is attached
    to, which is the only place they exist: a component interaction payload
    does not carry the embed.

    Responds DEFERRED so the DB write happens in a thread without risking the
    3s interaction deadline; the follow-up edit is the visible result.
    """
    if not _wl_add_allowed(payload):
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": "❌ You do not have the role required to add series to the whitelist."},
        )

    custom_id = data.get("custom_id", "")
    try:
        from modules.manhwa.discord.embeds import decode_whitelist_custom_id

        source, title_key = decode_whitelist_custom_id(custom_id)
    except Exception as e:
        logger.warn("wladd: bad custom_id", custom_id=custom_id[:60], err=str(e)[:160])
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": "❌ That button could not be read (malformed id). Re-run the release to get a fresh one.", "flags": EPHEMERAL},
        )

    message = payload.get("message", {}) or {}
    embeds = message.get("embeds", []) or []
    title = ""
    series_url = ""
    if embeds:
        title = str(embeds[0].get("title") or "").strip()
        series_url = str(embeds[0].get("url") or "").strip()

    user = (payload.get("member") or {}).get("user") or payload.get("user") or {}
    who = str(user.get("username") or user.get("id") or "unknown")

    from concurrent.futures import ThreadPoolExecutor

    def _write():
        from modules.manhwa.storage import whitelist as wl_store

        # The button lives on a RAW embed, which by definition means the series
        # is not whitelisted — but a re-click (or a retry) used to write the row
        # again and still answer "added", which reads as "I just added something
        # that already existed". Report the already-subscribed case instead.
        try:
            _existing = wl_store.get_whitelist_entry(title_key, source)
        except Exception:
            _existing = None
        if _existing:
            logger.info("wladd: already subscribed", source=source, title_key=title_key, by=who)
            return {"status": "ok", "already": True}

        res = wl_store.add_whitelist_entries([
            {
                "title_key": title_key,
                "source": source,
                "title": title or title_key,
                "series_url": series_url,
            }
        ])
        return res

    try:
        # Respond with type 4 (immediate), NOT type 5 (deferred).
        #
        # A deferred response MUST be followed by a call to the interaction's
        # follow-up endpoint, or Discord renders an EMPTY message — which is
        # exactly what the operator saw: the click landed, the row was written
        # (log: "wladd: subscribed"), and the channel showed a blank bubble.
        # Nothing in this handler ever issued the follow-up edit, so the
        # confirmation never appeared.
        #
        # The write is a single upsert measured at ~7ms (see the access log),
        # comfortably inside Discord's 3s interaction deadline, so there is no
        # need to defer at all. A deferral would only add a failure mode.
        with ThreadPoolExecutor(max_workers=1) as _ex:
            res = _ex.submit(_write).result(timeout=2.5)
        if str((res or {}).get("status")) != "ok":
            logger.warn("wladd: insert failed", source=source, title_key=title_key, err=str(res)[:200])
            return 200, _respond(
                CHANNEL_MESSAGE_WITH_SOURCE,
                {"content": f"❌ Could not add **{title or title_key}** — DB write failed. Try again in a few seconds.", "flags": EPHEMERAL},
            )
        if (res or {}).get("already"):
            logger.info("wladd: already subscribed", source=source, title_key=title_key, by=who)
            return 200, _respond(
                CHANNEL_MESSAGE_WITH_SOURCE,
                {
                    "content": f"ℹ️ **{title or title_key}** is already in the whitelist.\n"
                               f"Future chapters go to the whitelist feed — this series will not appear in raw.",
                    "flags": EPHEMERAL,
                },
            )
        logger.info("wladd: subscribed", source=source, title_key=title_key, by=who)
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": f"✅ **{title or title_key}** added to the whitelist.\n"
                        f"Future chapters go to the whitelist feed — this series will not appear in raw again.",
             "flags": EPHEMERAL},
        )
    except TimeoutError:
        logger.warn("wladd: DB timeout", source=source, title_key=title_key)
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": "⏳ Save is slow — try again in 5s (DB timeout).", "flags": EPHEMERAL},
        )
    except Exception as e:
        logger.warn("wladd failed", source=source, title_key=title_key, err=str(e)[:200])
        return 200, _respond(
            CHANNEL_MESSAGE_WITH_SOURCE,
            {"content": f"❌ Failed to add **{title or title_key}**: {str(e)[:160]}", "flags": EPHEMERAL},
        )


def _route_list_component(custom_id: str):
    parts = custom_id.split(":")
    page = int(parts[1]) if len(parts) > 1 else 1
    return 200, _respond(
        CHANNEL_MESSAGE_WITH_SOURCE,
        {"content": f"📄 Page {page} (list view WIP)"},
    )
