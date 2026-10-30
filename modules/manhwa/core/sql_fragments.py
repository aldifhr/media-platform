"""Shared SQL fragments that must not drift between call sites.

The whitelist<->recent_chapters join is the load-bearing one: whitelist.title_key
is slugified (dashes, migration 074) while recent_chapters.title_key keeps the
scraper's display form (spaces). A plain equality join drops rows that differ
only by that normalization, so every such join routes through here.
"""
from __future__ import annotations


def rc_wl_join(rc_alias: str = "rc", w_alias: str = "w") -> str:
    """ON-clause body matching `recent_chapters <rc_alias>` to
    `whitelist <w_alias>`, tolerant of space-vs-dash title_key normalization.

    Mirrors the authoritative predicate in
    app/storage/recent_chapters.py (claim_recent_chapters_for_dispatch).
    """
    return (
        f"{w_alias}.source = {rc_alias}.source AND ("
        f"{w_alias}.title_key = {rc_alias}.title_key OR "
        f"replace(normalize_title_key({w_alias}.title_key), ' ', '-') = "
        f"replace(normalize_title_key({rc_alias}.title_key), ' ', '-'))"
    )


RC_WL_JOIN_ON: str = rc_wl_join("rc", "w")
