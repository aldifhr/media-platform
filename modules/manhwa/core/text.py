"""Shared text normalization helpers."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata

from modules.manhwa.config import settings


def normalize_title_key(title: str) -> str:
    """Canonical normalized title key: lowercase, alnum+space, collapsed.

    Used to match whitelist entries against scraped chapter titles across
    all modules (pipeline, dashboard, compat). MUST be the only implementation.

    Non-Latin titles (CJK, Cyrillic, etc.) strip to an empty string under the
    ASCII-only rule, which produced empty title_keys and silently broke every
    downstream match. Accented Latin is transliterated first; anything that is
    still empty falls back to a stable hash so the key is never blank.
    """
    if not title:
        return ""
    raw = str(title)
    # PRIMARY rule is the legacy ASCII-only strip. It MUST stay the first
    # attempt: transliterating accents changes the slug ("pokémon" ->
    # "pokemon" instead of the historical "pok-mon"), which silently stops
    # matching the whitelist / latest_sent_chapter rows
    # already stored under the old slug and pushes the series back to RAW.
    t = html.unescape(raw.lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t:
        return t
    # Empty under the ASCII rule (non-Latin, e.g. CJK): try transliteration,
    # then fall back to a stable hash so title_key is never blank.
    t2 = unicodedata.normalize("NFKD", html.unescape(raw.lower()))
    t2 = "".join(c for c in t2 if not unicodedata.combining(c))
    t2 = re.sub(r"[^a-z0-9]+", " ", t2)
    t2 = re.sub(r"\s+", " ", t2).strip()
    if t2:
        return t2
    digest = hashlib.sha1(raw.strip().lower().encode("utf-8")).hexdigest()
    return f"t{digest[:15]}"

def slugify_title_key(title: str) -> str:
    """URL/path-safe title key: dashes instead of spaces.

    'eternally regressing knight' -> 'eternally-regressing-knight'.
    Use when putting a title_key in a URL path. Inverse of
    deslugify_title_key. Centralizes the dash<->space convention so we stop
    hand-rolling .replace('-',' ')/.replace(' ','-') at every call site.
    """
    return normalize_title_key(title).replace(" ", "-")

def deslugify_title_key(slug: str) -> str:
    """Reverse slugify_title_key: dashes -> spaces (URL path -> DB title_key).

    DB title_key uses spaces (legacy). Call once when receiving a slug from a
    URL path; never re-implement the replacement inline.
    """
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


_SHINIGAMI_HOST_RE = re.compile(r"https?://([^/]+)\.shinigami\.asia")


def normalize_shinigami_url(url: str | None) -> str | None:
    """Rewrite stale shinigami hostnames to current SECONDARY_PUBLIC_BASE.

    Any ``*.shinigami.asia`` host that does not match the current configured
    base is rewritten. To migrate after a subdomain change, only update
    ``SECONDARY_PUBLIC_BASE`` in ``.env``/``config.py``.
    """
    if not url or not isinstance(url, str):
        return url
    current_base = settings.SECONDARY_PUBLIC_BASE.rstrip("/")
    current_host = current_base.split("/")[-1]
    # current_host already contains the full *.shinigami.asia host, so
    # check directly instead of appending another ".shinigami.asia".
    if current_host in url:
        return url

    def _replace(m: re.Match[str]) -> str:
        host = m.group(1)
        if host == current_host:
            return m.group(0)
        return f"{m.group(0).split('://', 1)[0]}://{current_host}"

    return _SHINIGAMI_HOST_RE.sub(_replace, url)


def rewrite_shinigami_urls(row: dict, fields: tuple[str, ...]) -> dict:
    """Rewrite stale shinigami.asia URLs in ``row`` in place and return it.

    ``series_url`` is always rewritten; ``fields`` lists the additional keys
    (e.g. ``("url", "chapter_url", "cover")``) whose string values are
    rewritten when they still point at a stale host.
    """
    su = row.get("series_url")
    if su:
        row["series_url"] = normalize_shinigami_url(su) or su
    for fld in fields:
        v = row.get(fld)
        if isinstance(v, str) and "shinigami.asia" in v:
            row[fld] = normalize_shinigami_url(v) or v
    return row
