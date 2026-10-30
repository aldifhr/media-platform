"""Whitelist key/URL normalization and upstream metadata enrichment.

Extracted from app/services/whitelist_service.py. whitelist_service re-exports
these symbols for backward compatibility with existing call sites and tests.
"""

from __future__ import annotations

from modules.manhwa.logger import get_logger
from modules.manhwa.core.text import extract_uuid, normalize_shinigami_url, normalize_title_key

logger = get_logger("services:whitelist")


def _derive_title_key_from_url(url: str, title: str = "") -> str:
    """Best-effort title_key when the caller sent no explicit one.

    Rules, in order:
      * `/manga/<slug>` — do NOT adopt the URL slug. This is the ikiru-era
        path shape (slug generator dropped apostrophes: `a-gods-ascension`
        while the collector keyed off the title `a-god-s-ascension`), and no
        live source uses it. A row stored under the URL form can never match a
        scraped chapter, so the series would look whitelisted and never notify.
        Fall through to the title. Kept for any caller still passing that shape.
      * shinigami `/series/<uuid>` — resolve the UUID to its real title.
      * anything else — last path segment, unless it is a chapter slug.

    Returns "" when nothing usable is found; the caller then falls back to
    normalize_title_key(title).
    """
    if not url:
        return ""
    u = url.rstrip("/")
    key = ""
    if "/manga/" in u:
        key = ""
    elif "/series/" in u or "/chapter/" in u:
        _m = extract_uuid(u)
        if _m:
            try:
                from modules.manhwa.sources import shinigami as _sh_get

                _ser = _sh_get.get_shinigami_series(_m)
                if _ser and _ser.get("title"):
                    key = normalize_title_key(_ser["title"])
            except Exception as e:
                logger.warn("derive_title_key: shinigami fetch failed", err=str(e)[:120])
        if not key:
            key = ""
    else:
        key = u.split("/")[-1]
    if key.startswith("chapter-"):
        key = ""
    return key


def normalize_whitelist_urls(dry_run: bool = False) -> dict:
    """Rewrite stale shinigami hosts in whitelist + recent_chapters to current base."""
    from modules.manhwa.db import get_supabase as _sb

    wl_rows = (
        _sb().table("whitelist").select("title_key,source,series_url,url,cover").execute().data
        or []
    )
    rc_rows = (
        _sb().table("recent_chapters").select("id,series_url,chapter_url,cover").execute().data
        or []
    )

    def _apply(v):
        if not v or not isinstance(v, str) or "shinigami.asia" not in v:
            return v, False
        new_v = normalize_shinigami_url(v)
        return new_v, new_v != v

    wl_updates = []
    seen_wl = set()
    for r in wl_rows:
        key = (r.get("title_key"), r.get("source"))
        if key in seen_wl:
            continue
        seen_wl.add(key)
        changed = {}
        for fld in ("series_url", "url", "cover"):
            v = r.get(fld)
            if not v:
                continue
            new_v, changed_flag = _apply(v)
            if changed_flag:
                changed[fld] = new_v
        if changed:
            wl_updates.append((key, changed))

    rc_updates = []
    seen_rc = set()
    rc_chapter_seen = {}
    for r in rc_rows:
        rid = r.get("id")
        if rid in seen_rc:
            continue
        seen_rc.add(rid)
        changed = {}
        chapter_url_patch = None
        for fld in ("series_url", "chapter_url", "cover"):
            v = r.get(fld)
            if not v:
                continue
            new_v, changed_flag = _apply(v)
            if not changed_flag:
                continue
            if fld == "chapter_url":
                chapter_url_patch = new_v
            else:
                changed[fld] = new_v
        if chapter_url_patch is not None:
            if chapter_url_patch in rc_chapter_seen:
                continue
            rc_chapter_seen[chapter_url_patch] = rid
            changed["chapter_url"] = chapter_url_patch
        if changed:
            rc_updates.append((rid, changed))

    if not dry_run:
        for (tk, src), patch in wl_updates:
            _sb().table("whitelist").update(patch).eq("title_key", tk).eq("source", src).execute()
        for rid, patch in rc_updates:
            try:
                _sb().table("recent_chapters").update(patch).eq("id", rid).execute()
            except Exception as e:
                logger.warn(
                    "sync_whitelist_metadata: recent_chapters update failed",
                    rid=rid,
                    err=str(e)[:120],
                )

    return {
        "success": True,
        "dry_run": dry_run,
        "updated_whitelist": len(wl_updates),
        "updated_recent_chapters": len(rc_updates),
    }


def enrich_whitelist_entry(entry: dict, url: str, source: str, title: str) -> dict:
    """Enrich a whitelist entry with description/cover/genres from source API.

    Called by post_whitelist when fields are missing. Fetches directly from
    source API so the whitelist response immediately has metadata.
    """
    if (
        not entry.get("description")
        or not entry.get("cover")
        or not entry.get("genres")
        or not entry.get("rating")
        or not entry.get("origin")
    ):
        try:
            _url_for_meta = entry.get("series_url") or url or ""
            if source == "shinigami" and _url_for_meta:
                _m = extract_uuid(_url_for_meta)
                if _m:
                    from modules.manhwa.sources import shinigami as _sh2

                    meta = _sh2.get_shinigami_series_meta(_m)
                    if meta:
                        for k in ("cover", "rating", "genres", "description", "origin"):
                            if not entry.get(k) and meta.get(k):
                                entry[k] = meta[k]
                        if not entry.get("series_url") and meta.get("series_url"):
                            entry["series_url"] = meta["series_url"]
            # Fallback: search by title if still missing
            if (not entry.get("description") or not entry.get("cover")) and title:
                try:
                    if source == "shinigami":
                        from modules.manhwa.sources.shinigami import (
                            get_shinigami_series_meta as _gsm,
                            search_shinigami_api,
                        )

                        hits = search_shinigami_api(title, per_page=3)
                        for h in hits:
                            mid = h.get("manga_id")
                            if mid:
                                m2 = _gsm(mid)
                                if m2 and m2.get("description"):
                                    for k in (
                                        "cover",
                                        "rating",
                                        "genres",
                                        "description",
                                        "origin",
                                        "series_url",
                                    ):
                                        if not entry.get(k) and m2.get(k):
                                            entry[k] = m2[k]
                                    break
                except Exception as e:
                    logger.warn(
                        "enrich_whitelist_entry: search fallback failed",
                        title=title[:40],
                        source=source,
                        err=str(e)[:120],
                    )
        except Exception as e:
            logger.warn(
                "enrich_whitelist_entry: enrich failed",
                title=title[:40],
                source=source,
                err=str(e)[:120],
            )
    return entry
