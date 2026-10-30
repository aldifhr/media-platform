"""Whitelist write path: create, delete, patch and stale-row cleanup.

Extracted from app/services/whitelist_service.py. whitelist_service re-exports
these symbols for backward compatibility with existing call sites.
"""

from __future__ import annotations

from modules.manhwa.logger import get_logger
from modules.manhwa.services.whitelist.normalize import _derive_title_key_from_url, enrich_whitelist_entry
from modules.manhwa.storage import whitelist as wl_store
from modules.manhwa.core.text import escape_like, is_uuid_title_key, normalize_title_key, slugify_title_key

logger = get_logger("services:whitelist")


def post_whitelist(
    title: str, url: str, source: str = "shinigami", body: dict | None = None
) -> dict:
    """Add a whitelist entry with enrichment."""

    title_key = body.get("title_key") or "" if body else ""
    if not title_key:
        title_key = _derive_title_key_from_url(url, title)
    if not title_key:
        title_key = normalize_title_key(title)

    # UUID / spaced lower cause merge false + delete mismatches — enforce slug here
    # prefer seriesUrl slug over title slug — rss uses seriesUrl slug
    if title_key and is_uuid_title_key(title_key):
        logger.warn(
            "post_whitelist: UUID title_key detected, expected slug",
            title_key=title_key[:16],
            title=(title or "")[:40],
        )
        _su2 = (body.get("seriesUrl") or body.get("series_url") or url or "") if body else ""
        _slug2 = ""
        if _su2 and "/series/" in _su2:
            _slug2 = _su2.rstrip("/").split("/")[-1].split("?")[0]
            _slug2 = slugify_title_key(_slug2)
        if _slug2 and not is_uuid_title_key(_slug2):
            title_key = _slug2
        else:
            _slug_from_title = slugify_title_key(title) if title else ""
            if _slug_from_title:
                title_key = _slug_from_title
    # enforce canonical slug: normalize_title_key (alnum+space collapse) then dash
    title_key = slugify_title_key(title_key) if title_key else ""

    entry = {"title": title, "title_key": title_key, "source": source}
    _series_meta_extra: dict = {}
    if body:
        for f in ("cover", "rating", "origin", "genres", "description", "type"):
            v = body.get(f)
            if v is None or v == "":
                continue
            if f == "description" and isinstance(v, str) and len(v.strip()) < 4:
                continue
            if (
                f == "genres"
                and isinstance(v, list)
                and (
                    len(v) == 0 or (len(v) == 1 and str(v[0]).strip().lower() in ("a", "x", "n/a"))
                )
            ):
                continue
            _series_meta_extra[f] = v
        _su = body.get("seriesUrl") or body.get("series_url") or ""
        if isinstance(_su, str) and _su.strip():
            entry["series_url"] = _su.strip()

    # Enrich missing fields — non-blocking: insert immediately, enrich in background
    # so POST stays 0.02s instead of 0.5s (was 3 sync HTTP fetches to upstream APIs).
    # Insert into whitelist FIRST (series_meta has FK → whitelist)
    res = wl_store.add_whitelist_entries([entry])
    # Now upsert static fields into series_meta (FK will be satisfied)
    if _series_meta_extra:
        try:
            from modules.manhwa.db import get_supabase as _sm_sb

            _sm_sb().table("series_meta").upsert(
                {"title_key": title_key, "source": source, **_series_meta_extra},
                on_conflict="title_key,source",
            ).execute()
        except Exception as e:
            logger.warn(
                "post_whitelist: series_meta upsert failed",
                title_key=title_key,
                source=source,
                err=str(e)[:120],
            )
    _needs_enrich = (
        not _series_meta_extra.get("description")
        or not _series_meta_extra.get("cover")
        or not _series_meta_extra.get("genres")
    )
    if _needs_enrich:
        try:
            import threading

            _entry_copy = dict(entry)
            _url_copy = url
            _source_copy = source
            _title_copy = title

            def _bg_enrich():
                try:
                    enriched = enrich_whitelist_entry(
                        dict(_entry_copy), _url_copy, _source_copy, _title_copy
                    )
                    # Only update series_meta if enrich added static fields (whitelist minimal)
                    _sm_update = {
                        k: v
                        for k, v in enriched.items()
                        if k in ("cover", "rating", "genres", "description", "type", "origin")
                        and v not in (None, "", [])
                    }
                    if _sm_update:
                        try:
                            from modules.manhwa.db import get_supabase as _sm2

                            _sm2().table("series_meta").upsert(
                                {"title_key": title_key, "source": source, **_sm_update},
                                on_conflict="title_key,source",
                            ).execute()
                        except Exception as e:
                            logger.warn(
                                "post_whitelist: bg enrich series_meta upsert failed",
                                title_key=title_key,
                                source=source,
                                err=str(e)[:120],
                            )
                except Exception as e:
                    logger.warn(
                        "post_whitelist: bg enrich failed",
                        title_key=title_key,
                        source=source,
                        err=str(e)[:120],
                    )

            threading.Thread(target=_bg_enrich, daemon=True).start()
        except Exception as e:
            logger.warn(
                "post_whitelist: bg enrich thread start failed",
                title_key=title_key,
                source=source,
                err=str(e)[:120],
            )
    return res


def delete_whitelist(
    title_key: str = "", source: str = "", id: str = "", title: str = "", url: str = ""
) -> dict:
    """Delete a whitelist entry (and its recent_chapters rows) by ANY key.

    Matches on id OR title_key OR canonical_title_key OR title (ILIKE) OR url
    (ILIKE), plus source when provided. Returns {"status":"ok","deleted":N} with
    the real row count, or raises KeyError-style via returning a 404-shaped dict
    when nothing matched (caller maps to 404). Never returns a silent 200 no-op.

    Also deletes the matching recent_chapters rows so the FE reader (which
    materializes whitelist from recent_chapters) reflects the deletion instead of
    showing a stale entry that lives only in recent_chapters.
    """
    from modules.manhwa.db import get_supabase

    sb = get_supabase()
    _tk = (title_key or "").strip()
    _src = (source or "").strip()
    _id = (id or "").strip()
    _title = (title or "").strip()
    _url = (url or "").strip()

    logger.info(
        "delete_whitelist called",
        id=_id[:16] if _id else "",
        title_key=_tk,
        title=_title[:40] if _title else "",
        url=_url[:60] if _url else "",
        source=_src,
    )

    # Build a permissive OR filter for the whitelist table.
    # We can't do a single .or_() easily across all column types with db_adapter,
    # so we try each key and union the matched ids.
    matched_ids: set[str] = set()
    try:
        if _id:
            r = sb.table("whitelist").select("id, title_key, source").eq("id", _id).execute()
            for x in r.data or []:
                matched_ids.add(x["id"])
        if _tk:
            r = sb.table("whitelist").select("id, title_key, source").eq("title_key", _tk).execute()
            for x in r.data or []:
                matched_ids.add(x["id"])
            # canonical variant (space<->dash)
            r = (
                sb.table("whitelist")
                .select("id, title_key, source")
                .eq("title_key", _tk.replace(" ", "-"))
                .execute()
            )
            for x in r.data or []:
                matched_ids.add(x["id"])
            r = (
                sb.table("whitelist")
                .select("id, title_key, source")
                .eq("title_key", _tk.replace("-", " "))
                .execute()
            )
            for x in r.data or []:
                matched_ids.add(x["id"])
        if _title:
            # Escape LIKE wildcards to avoid over-match + use exact title_key fallback first
            _esc_title = escape_like(_title)
            # Prefer exact title_key match via normalized title before ILIKE
            from modules.manhwa.core.text import normalize_title_key as _ntk_del

            _ntk_title = _ntk_del(_title)
            if _ntk_title:
                r = (
                    sb.table("whitelist")
                    .select("id, title_key, source")
                    .eq("title_key", _ntk_title)
                    .execute()
                )
                for x in r.data or []:
                    matched_ids.add(x["id"])
            r = (
                sb.table("whitelist")
                .select("id, title_key, source")
                .ilike("title", f"%{_esc_title}%")
                .execute()
            )
            for x in r.data or []:
                matched_ids.add(x["id"])
        if _url:
            _esc_url = escape_like(_url.split("/")[-1])
            r = (
                sb.table("whitelist")
                .select("id, title_key, source")
                .ilike("url", f"%{_esc_url}%")
                .execute()
            )
            for x in r.data or []:
                matched_ids.add(x["id"])
    except Exception as _e:
        logger.warn("delete_whitelist: match query failed", err=str(_e)[:160])

    # Apply source filter if provided — bulk (1 query, bukan N)
    if _src and matched_ids:
        try:
            r = (
                sb.table("whitelist")
                .select("id, source")
                .in_("id", list(matched_ids))
                .eq("source", _src)
                .execute()
            )
            matched_ids = {x["id"] for x in (r.data or [])}
        except Exception as _e:
            logger.warn("delete_whitelist: source filter failed", err=str(_e)[:120])
            matched_ids = set()

    # Delete via Supabase client (atomic per table, separate calls)
    deleted = 0
    rc_deleted = 0
    try:
        if matched_ids:
            r = sb.table("whitelist").delete().in_("id", list(matched_ids)).execute()
            deleted = len(r.data or [])
    except Exception as _e:
        logger.warn("delete_whitelist: whitelist delete failed", err=str(_e)[:160])

    try:
        from modules.manhwa.db import q as _q

        _conds = []
        _params: list = []
        if _tk:
            _conds.append("title_key = %s")
            _params.append(_tk)
            _conds.append("title_key = %s")
            _params.append(_tk.replace("-", " "))
        if _title:
            _conds.append("title ILIKE %s")
            _params.append(f"%{_title}%")
        if _url:
            _slug = _url.rstrip("/").split("/")[-1]
            if _slug:
                _conds.append("series_url ILIKE %s")
                _params.append(f"%{_slug}%")
                _conds.append("chapter_url ILIKE %s")
                _params.append(f"%{_slug}%")
        if not _conds:
            rc_deleted = 0
        else:
            _where = " OR ".join(f"({c})" for c in _conds)
            if _src:
                _where = f"({_where}) AND source = %s"
                _params.append(_src)
            _rows = _q(f"DELETE FROM recent_chapters WHERE {_where} RETURNING id", _params)
            rc_deleted = len(_rows or [])
    except Exception as _e:
        logger.warn("delete_whitelist: recent_chapters delete failed", err=str(_e)[:160])

    total_deleted = deleted + rc_deleted
    logger.info(
        "delete_whitelist result",
        whitelist_deleted=deleted,
        recent_chapters_deleted=rc_deleted,
        matched_ids=len(matched_ids),
    )
    if total_deleted == 0:
        return {"status": "not_found", "deleted": 0, "error": "no matching whitelist entry"}
    # Invalidate the cached whitelist so the next GET reflects the deletion
    # immediately (load_whitelist uses @ttl_cache(30s); without this the FE
    # verify step sees a stale list and reports "stillThere true").
    try:
        from modules.manhwa.storage import whitelist as _wl_store

        _wl_store.load_whitelist.invalidate()
    except Exception as _ie:
        logger.warn("delete_whitelist: cache invalidate failed", err=str(_ie)[:120])
    return {
        "status": "ok",
        "deleted": total_deleted,
        "whitelist": deleted,
        "recent_chapters": rc_deleted,
    }


def patch_whitelist(title_key: str, source: str = "", updates: dict | None = None) -> dict:
    """Update mutable whitelist fields."""
    from modules.manhwa.db import get_supabase

    if not updates:
        return {"success": True, "updated": 0, "note": "no fields to update"}

    if updates and "title_key" in updates and updates["title_key"]:
        updates = dict(updates)
        updates["title_key"] = slugify_title_key(str(updates["title_key"]))
    # also normalize the lookup key itself (caller may pass spaced title)
    title_key = slugify_title_key(title_key) if title_key else title_key

    sb = get_supabase()
    try:
        q = sb.table("whitelist").update(updates).eq("title_key", title_key)
        if source:
            q = q.eq("source", source)
        res = q.execute()
        updated = len(res.data or []) if hasattr(res, "data") else 0
        return {"success": True, "updated": updated, "title_key": title_key}
    except Exception as e:
        msg = str(e)
        if "chk_tk_slug" in msg:
            logger.warn(
                "patch_whitelist: chk_tk_slug violation",
                err=msg[:300],
                title_key=title_key[:40],
            )
            return {
                "success": False,
                "updated": 0,
                "error": "title_key must be slug [a-z0-9-]",
                "detail": msg[:300],
            }
        raise


def auto_cleanup_stale_whitelist(days: int = 30, dry_run: bool = False) -> dict:
    """Remove whitelist entries that were added >`days` ago AND have NEVER
    been notified (no dispatch_history row at all)."""
    return wl_store.auto_cleanup_stale_whitelist(days=days, dry_run=dry_run)
