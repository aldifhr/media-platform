"""FCFS key primitives — pure, dependency-free.

Canonical title+chapter dedup key. Lives in core so storage and services can
both use it without a layering violation. services/fcfs.py re-exports these.
"""
from __future__ import annotations

import re


def normalize_title(title: str) -> str:
    """Canonical title for FCFS — match whitelist slugify_title_key."""
    from modules.manhwa.core.text import slugify_title_key as _slugify
    return _slugify(str(title or ""))


def normalize_chapter(ch_str: str | int | float | None) -> str:
    """Canonical chapter token: 12.50 → 12.5, 160-2 → 160.2, 012 → 12.

    `None` and "" collapse to "", but a numeric 0 stays "0": the previous
    `str(ch_str or "")` treated int 0 as absent because 0 is falsy, so
    normalize_chapter(0) returned "" while normalize_chapter("0") returned "0".
    Those two produce DIFFERENT fcfs_keys, so the same chapter reported by one
    source as a number and another as a string would not dedupe — a duplicate
    notification. No chapter 0 exists in the data yet; this is a latent trap,
    not an active bug.
    """
    if ch_str is None:
        return ""
    s = str(ch_str).strip()
    if not s:
        return s
    m = re.match(r"^(\d+)(?:[.\-](\d+))?$", s)
    if not m:
        # e.g. "OVA", "Extra" — keep as-is (preserve case) for backward compat
        return s
    whole = int(m.group(1))
    if m.group(2) is None:
        return str(whole)
    frac = m.group(2).rstrip("0")
    if not frac:
        return str(whole)
    return f"{whole}.{frac}"


def fcfs_key(title: str, chapter: str | int | float | None) -> str:
    """Stable cross-source dedupe key: normalized title + normalized chapter."""
    return f"{normalize_title(title)}#{normalize_chapter(chapter)}"


def normalize_chapter_num(chapter) -> str:
    """Canonical numeric chapter string via ``"%.10g"`` (12.50 → "12.5").

    Numeric-only canonicalization used by storage composite keys. Unlike
    ``normalize_chapter`` it does not handle ``160-2`` ranges; it is the
    ``%.10g`` convention that recent_chapters/runner/senders share.
    """
    try:
        return "%.10g" % float(chapter)
    except (TypeError, ValueError):
        return str(chapter or "")


def fcfs_key_for_item(item: dict) -> str:
    """Build an fcfs_key from a chapter/item dict.

    Centralizes the load-bearing ``title_key`` dash→space conversion that
    must match the watchdog; do not re-type ``.replace("-", " ")`` inline.
    """
    title = str(item.get("title_key") or "").replace("-", " ")
    return fcfs_key(title, item.get("chapter") or "")


def parse_chapter_number(ch: str | int | float | None) -> float | None:
    """Numeric value for sorting/filtering (float), or None if unparseable."""
    if ch is None:
        return None
    if isinstance(ch, (int, float)):
        return float(ch)
    m = re.search(r"(\d+(?:\.\d+)?)", str(ch).strip())
    return float(m.group(1)) if m else None
