"""Discord embed builder (parity with lib/discord/embed-builder.ts)."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

# Same-origin image proxy so Discord can load covers that would
# otherwise 403 on hotlink (sources block cross-origin img).
# Mirror of the frontend rewriteCoverUrl() logic.
from urllib.parse import quote as _urlquote, urlparse

# Short-hash lookup for whitelist custom_ids that exceed Discord's 100-char
# limit. See encode_whitelist_custom_id / decode_whitelist_custom_id.
# In-process map is the fast path; Redis is the shared source of truth so a
# button authored by the cron process still resolves in the API process.
_wl_custom_id_map: dict[str, tuple[str, str]] = {}
_WL_HASH_RE = re.compile(r"^h[0-9a-f]{16}$")
_WL_HASH_TTL_S = 7 * 24 * 60 * 60


def _wl_hash_redis():
    try:
        from modules.manhwa.tasks.queue import _get_redis

        return _get_redis()
    except Exception:
        return None


def _wl_hash_store(h: str, source: str, slug: str) -> None:
    _wl_custom_id_map[h] = (source, slug)
    r = _wl_hash_redis()
    if r is None:
        return
    try:
        import json

        r.setex(f"wladd:hash:{h}", _WL_HASH_TTL_S, json.dumps({"source": source, "slug": slug}))
    except Exception:
        pass


def _wl_hash_lookup(h: str) -> tuple[str, str] | None:
    entry = _wl_custom_id_map.get(h)
    if entry:
        return entry
    r = _wl_hash_redis()
    if r is None:
        return None
    try:
        import json

        raw = r.get(f"wladd:hash:{h}")
        if raw:
            data = json.loads(raw)
            source = str(data.get("source") or "")
            slug = str(data.get("slug") or "")
            if source and slug:
                _wl_custom_id_map[h] = (source, slug)
                return source, slug
    except Exception:
        pass
    return None


try:
    from modules.manhwa.config import settings as _settings
    _PUBLIC_BASE = (_settings.PUBLIC_BASE_URL or "https://scanner.aldifhr.my.id").rstrip("/")
except Exception:
    _PUBLIC_BASE = "https://scanner.aldifhr.my.id"


def _escape_discord_md(text: str) -> str:
    """Escape Discord markdown characters in URLs to prevent injection."""
    return text.replace("\\", "\\\\").replace("]", "\\]").replace(")", "\\)")


def _proxy_cover(cover: str | None) -> str | None:
    """Return the cover URL for Discord embeds.

    Discord's embed image fetcher cannot send our monitor auth token, so the
    authenticated /reader/proxy returns 401 and covers render blank. Use the
    PUBLIC /api/reader/cover endpoint instead — it serves the same
    allowlisted, size-capped, cached image bytes without auth, so Discord can
    load the cover directly.
    """
    if not cover or not isinstance(cover, str):
        return cover
    cover = cover.strip()
    if not cover:
        return cover
    # Already proxied — return as-is
    if "/api/v1/reader/cover-img" in cover or "/api/reader/cover-img" in cover:
        return cover
    public_base = (_PUBLIC_BASE or "https://scanner.aldifhr.my.id").rstrip("/")
    # Covers may be stored as a same-origin /api/reader/proxy?url=<enc
    # upstream> wrapper (so the FE fetches them through the authed proxy).
    # Discord needs the DIRECT upstream URL wrapped in the PUBLIC cover-img
    # proxy instead — unwrap the inner url first, else the double-wrap 403s.
    if ("/api/reader/proxy" in cover or "/api/v1/reader/proxy" in cover) and "url=" in cover:
        from urllib.parse import urlparse as _up, parse_qs as _pqs, unquote as _uq
        _inner = _pqs(_up(cover).query).get("url", [""])[0]
        if _inner:
            cover = _uq(_inner)
    # Route source covers through our public proxy. Discord accepted some direct
    # CDN URLs but stored width=0/height=0 on real messages, leaving blank covers.
    # Direct URLs — wrap in public cover-img proxy
    # Fix double-encode: RSS cover already %2F-encoded (X-Amz-Credential), _urlquote with safe='' would %→%25. Normalize first.
    from urllib.parse import unquote as _uq2
    try:
        # Normalize once to avoid %2F → %252F
        _norm = _uq2(cover)
        return f"{public_base}/api/v1/reader/cover-img?url={_urlquote(_norm, safe='')}"
    except Exception:
        return f"{public_base}/api/v1/reader/cover-img?url={_urlquote(cover, safe='')}"


SOURCE_COLORS = {
    # Mirrors the frontend source palette in apps/frontend/lib/styles.ts so a
    # source reads the same colour in Discord and in the UI.
    # A source missing here silently falls back to grey — add it with the
    # others or it looks untracked.
    "shinigami": 0xEF4444,  # red-500
    "ikiru": 0x22C55E,  # green-500
}

SOURCE_LABELS = {
    "shinigami": "Shinigami",
    "ikiru": "Ikiru",
}

STAR_FILLED = "⭐"
STAR_EMPTY = "☆"



def _rating_stars(rating) -> str:
    if rating in (None, "", "N/A"):
        return "`No rating`"
    try:
        num = float(rating)
    except (TypeError, ValueError):
        return "`No rating`"
    filled = max(0, min(5, round(num / 2)))
    display = int(num) if num.is_integer() else f"{num:.1f}"
    return f"{STAR_FILLED * filled}{STAR_EMPTY * (5 - filled)} `{display}/10`"


def _short_synopsis(text: str) -> str | None:
    if not text:
        return None
    d = str(text).strip()
    if d.lower() in ("unknown", "n/a"):
        return None
    # Shinigami's API provides concise aliases like "Pemain Beku", which are
    # valid synopses, not URLs/slugs; retain them as the RAW feed description.
    # strip html tags
    import re
    clean = re.sub(r"<[^>]*>", "", d).strip()
    if len(clean) <= 220:
        return clean
    sub = clean[:220]
    last_space = sub.rfind(" ")
    return f"{sub[:last_space if last_space > 160 else 220].strip()}..."


def _truncate(text: str, n: int = 200) -> str:
    text = (text or "").strip()
    return text[:n] + "..." if len(text) > n else text


def _release_timestamps(updated_time: str | None) -> str:
    if not updated_time:
        return "Unknown"
    try:
        # psycopg returns datetime objects for timestamptz columns (claim path
        # uses SELECT *), not strings. Handle both.
        if isinstance(updated_time, datetime):
            secs = int(updated_time.timestamp())
            return f"<t:{secs}:R> (<t:{secs}:F>)"
        s = str(updated_time)
        if s.isdigit():
            secs = int(s) / 1000 if len(s) > 11 else int(s)
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            secs = int(dt.timestamp())
        return f"<t:{secs}:R> (<t:{secs}:F>)"
    except Exception:
        return "Unknown"


def build_chapter_embed(
    title: str,
    chapter: str,
    url: str = "",
    series_url: str = "",
    source: str = "",
    cover: str = "",
    rating: str = "",
    genres: list | None = None,
    description: str = "",
    updated_time: str = "",
) -> dict:
    return _build_embed(
        title=title,
        chapters=[str(chapter)],
        chapter_urls=[url],
        series_url=series_url,
        source=source,
        cover=cover,
        rating=rating,
        genres=genres,
        description=description,
        updated_time=updated_time,
    )


def build_multi_chapter_embed(
    title: str,
    chapters: list[str],
    chapter_urls: list[str],
    series_url: str = "",
    source: str = "",
    cover: str = "",
    rating: str = "",
    genres: list | None = None,
    description: str = "",
    updated_time: str = "",
) -> dict:
    """One embed for a title that dropped MULTIPLE chapters in 24h.

    Groups all chapters into a single Discord message (instead of N messages,
    one per chapter) to stay under daily message limits. Shows the chapter
    list and links to the latest one.
    """
    return _build_embed(
        title=title,
        chapters=[str(c) for c in chapters],
        chapter_urls=chapter_urls,
        series_url=series_url,
        source=source,
        cover=cover,
        rating=rating,
        genres=genres,
        description=description,
        updated_time=updated_time,
    )


def add_whitelist_button(
    source: str = "",
    title_key: str = "",
    title: str = "",
    series_url: str = "",
) -> list[dict]:
    """The "Add to whitelist" button row for a RAW-feed chapter embed."""
    if not title_key or not source:
        return []
    from modules.manhwa.core.text import slugify_title_key
    return [
        {
            "type": 1,  # ACTION_ROW
            "components": [
                {
                    "type": 2,  # BUTTON
                    "style": 3,  # SUCCESS (green)
                    "label": "Add to whitelist",
                    "custom_id": encode_whitelist_custom_id(source, title_key),
                },
                {
                    "type": 2,  # BUTTON
                    "style": 4,  # DANGER (red)
                    "label": "Exclude",
                    "custom_id": f"exclude:{source}:{slugify_title_key(title_key)}",
                },
            ],
        }
    ]


def encode_whitelist_custom_id(source: str, title_key: str) -> str:
    """`wladd:<source>:<payload>` — payload is either the slug or a short hash.

    Discord caps custom_id at 100 chars. Non-ASCII slugs are base64url'd and
    can blow past that, so long keys fall back to a 16-char sha1 prefix that
    the handler resolves through `_wl_custom_id_map` or recent_chapters.
    """
    import base64
    import hashlib
    from modules.manhwa.core.text import slugify_title_key

    slug = slugify_title_key(title_key)
    if re.fullmatch(r"[a-z0-9-]+", slug or ""):
        payload = slug
    else:
        payload = "b64:" + base64.urlsafe_b64encode(slug.encode()).decode().rstrip("=")
    cid = f"wladd:{source}:{payload}"
    if len(cid) <= 100:
        return cid
    # Too long — store a short hash and resolve it on decode.
    h = hashlib.sha1(f"{source}:{slug}".encode()).hexdigest()[:16]
    _wl_hash_store(h, source, slug)
    return f"wladd:{source}:h{h}"


def decode_whitelist_custom_id(custom_id: str) -> tuple[str, str]:
    """Inverse of encode_whitelist_custom_id → (source, title_key).

    Raises ValueError on a malformed id. The handler must not guess here: a
    bad id means the button was authored by an older build or hand-edited, and
    silently defaulting would whitelist an arbitrary series.
    """
    import base64
    from modules.manhwa.core.text import slugify_title_key

    parts = (custom_id or "").split(":")
    if len(parts) != 3 or parts[0] != "wladd":
        raise ValueError(f"malformed custom_id: {custom_id!r}")
    source, payload = parts[1], parts[2]
    if not source or not payload:
        raise ValueError(f"empty source or title_key in custom_id: {custom_id!r}")
    # Only a real short hash (h + 16 hex) is a hash reference. A slug that
    # merely starts with "h" (e.g. "hunter-x-hunter") must NOT be treated as
    # one — that used to resolve through recent_chapters limit(1) and
    # subscribed an arbitrary series.
    if _WL_HASH_RE.fullmatch(payload):
        entry = _wl_hash_lookup(payload[1:])
        if entry:
            return entry
        raise ValueError(f"unknown hash in custom_id: {custom_id!r}")
    if payload.startswith("b64:"):
        pad = "=" * (-len(payload[4:]) % 4)
        payload = base64.urlsafe_b64decode(payload[4:] + pad).decode()
    return source, slugify_title_key(payload)


# Pre-compiled regex for chapter number extraction (called 2x per embed build).
_CHAPTER_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _build_embed(
    title: str,
    chapters: list[str],
    chapter_urls: list[str],
    series_url: str = "",
    source: str = "",
    cover: str = "",
    rating: str = "",
    genres: list | None = None,
    description: str = "",
    updated_time: str = "",
) -> dict:
    label = SOURCE_LABELS.get(source, source or "unknown")
    color = SOURCE_COLORS.get(source, 0x95A5A6)
    genres_text = ", ".join((genres or [])[:5]) if genres else None
    rating_display = _rating_stars(rating) if rating else "`No rating`"

    # Decode HTML entities (e.g. "I&#8217;m" → "I'm")
    title_clean = html.unescape(title or "")
    description_clean = html.unescape(description or "")
    if genres_text:
        genres_text = html.unescape(genres_text)

    # Chapter field: list all chapters, link the latest one.
    _chapters_sorted = sorted(
        zip(chapters, chapter_urls),
        key=lambda x: float(m.group(1)) if (m := _CHAPTER_NUM_RE.search(x[0])) else 0,
    )
    _chapter_links = []
    for _ch, _url in _chapters_sorted:
        if _url:
            _chapter_links.append(f"[Chapter {_ch}]({_escape_discord_md(_url)})")
        else:
            _chapter_links.append(f"Chapter {_ch}")
    _latest_url = _chapters_sorted[-1][1] if _chapters_sorted else ""
    _latest_tracked = _latest_url
    _multi = len(_chapters_sorted) > 1
    _chapter_value = ", ".join(_chapter_links) if _multi else (_chapter_links[0] if _chapter_links else "—")

    fields = [
        {"name": "🕐 Released", "value": _release_timestamps(updated_time), "inline": False},
        {"name": "📖 Chapter" + ("s" if _multi else ""), "value": _chapter_value, "inline": True},
        {"name": "🔗 Source", "value": f"`{label}`", "inline": True},
    ]
    if genres_text:
        fields.append({"name": "🏷️ Genres", "value": f"`{genres_text}`", "inline": False})
    fields.append({"name": "⭐ Rating", "value": rating_display, "inline": True})

    action_parts = []
    if _latest_tracked:
        action_parts.append(f"[📖 Read Latest]({_escape_discord_md(_latest_tracked)})")
    if series_url and series_url != _latest_url:
        action_parts.append(f"[📚 Series Page]({_escape_discord_md(series_url)})")
    action_line = f"**Links:** {' • '.join(action_parts)}" if action_parts else ""
    synopsis_text = _short_synopsis(description_clean)
    desc = ""
    if synopsis_text:
        desc = synopsis_text + (f"\n{action_line}" if action_line else "")
    elif action_line:
        desc = action_line

    embed = {
        "title": _truncate(title_clean, 200) or "Untitled",
        "url": series_url or None,
        "color": color,
        "fields": fields,
        "footer": {"text": f"Source: {label}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if desc:
        embed["description"] = desc
    if cover and (cover.startswith("http") or cover.startswith("/api/reader/") or cover.startswith("/api/v1/reader/") or cover.strip()):
        _thumb = _proxy_cover(cover)
        # Discord fetches an embed thumbnail asynchronously, AFTER accepting the
        # message. Two messages carrying a BYTE-IDENTICAL thumbnail URL get two
        # concurrent fetches of that URL, and one can come back empty — Discord
        # then renders the embed with no image and does not retry. Observed
        # live: ch 345 and 346 of one series dispatched 8ms apart
        # (21:29:58.027 and .033) sharing the series cover, so both embeds
        # referenced the exact same URL and 345 rendered blank while 346 got the
        # image. A series releasing 2+ chapters in one pass is routine here
        # (recent runs sent 2, 2, 4), so this was not a one-off.
        #
        # The buster goes BEFORE url=, never after. reader_proxy() takes the raw
        # remainder when the query starts with "url=" (to preserve encoding), so
        # anything appended after it leaks into the upstream URL and S3 rejects
        # the signature — verified: that returns a 1x1 transparent PNG
        # placeholder. Leading with v= makes the query start with "v=", so the
        # proxy parses url= normally and the image is unaffected.
        if _thumb and chapters:
            # Keep signed Ikiru CDN URLs byte-for-byte intact; adding a cache
            # buster changes their signed query and invalidates the image URL.
            _host = (urlparse(_thumb).hostname or "").lower()
            if _host != "cdn.ikiru.id":
                _ch_tag = _urlquote(str(chapters[-1] or "0"), safe="")
                _thumb = _thumb.replace("?", f"?v={_ch_tag}&", 1) if "?" in _thumb else f"{_thumb}?v={_ch_tag}"
        embed["thumbnail"] = {"url": _thumb}
    return embed
