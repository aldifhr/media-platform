"""Manhwa text normalization (pure). Ported verbatim from manhwa-scanner
app/core/text.py — the ASCII-first title_key rule is load-bearing for
whitelist/latest_sent matching and MUST NOT change without tests.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata


def normalize_title_key(title: str) -> str:
    """Canonical normalized title key: lowercase, alnum+space, collapsed.

    PRIMARY rule is the legacy ASCII-only strip (must stay first: transliterating
    accents changes the slug and breaks matching against stored title_keys).
    Non-Latin titles strip empty under ASCII, then try transliteration, then fall
    back to a stable hash so the key is never blank.
    """
    if not title:
        return ""
    raw = str(title)
    t = html.unescape(raw.lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t:
        return t
    t2 = unicodedata.normalize("NFKD", html.unescape(raw.lower()))
    t2 = "".join(c for c in t2 if not unicodedata.combining(c))
    t2 = re.sub(r"[^a-z0-9]+", " ", t2)
    t2 = re.sub(r"\s+", " ", t2).strip()
    if t2:
        return t2
    digest = hashlib.sha1(raw.strip().lower().encode("utf-8")).hexdigest()
    return f"t{digest[:15]}"


def slugify_title_key(title: str) -> str:
    """URL/path-safe title key: dashes instead of spaces."""
    return normalize_title_key(title).replace(" ", "-")


def deslugify_title_key(slug: str) -> str:
    """Reverse slugify_title_key: dashes -> spaces (URL path -> DB title_key)."""
    if not slug:
        return ""
    return slug.replace("-", " ").strip()


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_UUID_SEARCH_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def is_uuid_title_key(value: str | None) -> bool:
    """True when ``value`` is a bare legacy UUID title_key."""
    return bool(value) and bool(_UUID_RE.match(str(value)))


def extract_uuid(text: str | None) -> str | None:
    """Return the first UUID found in ``text`` (e.g. a series URL), else None."""
    if not text:
        return None
    m = _UUID_SEARCH_RE.search(str(text))
    return m.group(1) if m else None


def escape_like(value: str | None) -> str:
    """Escape LIKE/ILIKE wildcards so ``%`` and ``_`` match literally."""
    if not value:
        return ""
    return str(value).replace("%", r"\%").replace("_", r"\_")


def strip_html(text: str | None) -> str:
    """Remove HTML tags, unescape entities and collapse whitespace."""
    if not text:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub("", str(text)))).strip()


def slug_from_series_url(url: str | None) -> str:
    """Last non-empty path segment of a series URL (the source-side id)."""
    if not url:
        return ""
    return str(url).rstrip("/").split("/")[-1]
