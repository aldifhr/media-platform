"""FCFS dedup primitives — pure, dependency-free. Ported verbatim from
manhwa-scanner app/core/fcfs.py. These define cross-source title+chapter
dedup semantics; DO NOT change without tests demonstrating correctness.
"""

from __future__ import annotations

import re

from modules.manhwa.domain.text import slugify_title_key


def normalize_title(title: str) -> str:
    """Canonical title for FCFS — match whitelist slugify_title_key."""
    return slugify_title_key(str(title or ""))


def normalize_chapter(ch_str: str | int | float | None) -> str:
    """Canonical chapter token: 12.50 -> 12.5, 160-2 -> 160.2, 012 -> 12.

    ``None`` and "" collapse to "", but a numeric 0 stays "0" (a number 0 and
    string "0" must produce the SAME key or the same chapter from two sources
    would not dedupe).
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
    """Canonical numeric chapter string via ``"%.10g"`` (12.50 -> "12.5")."""
    try:
        return "%.10g" % float(chapter)  # noqa: UP031  (verbatim port; == {:.10g})
    except (TypeError, ValueError):
        return str(chapter or "")


def fcfs_key_for_item(item: dict) -> str:
    """Build an fcfs_key from a chapter/item dict.

    Centralizes the load-bearing ``title_key`` dash->space conversion that must
    match the watchdog; do not re-type ``.replace("-", " ")`` inline.
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
