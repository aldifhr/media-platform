"""Ingest — batch_insert for recent_chapters (deep)."""
from datetime import datetime

from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger
from modules.manhwa.core.origin import normalize_origin

logger = get_logger("storage:recent-chapters:ingest")

# re-import helpers from caches/window for locality
from modules.manhwa.storage.recent_chapters_caches import (
    _composite_key,
    _get_wl_origins,
    _load_existing_rc,
)
from modules.manhwa.storage.recent_chapters_window import prune_older_than  # noqa: F401 — keep import for compat


ALLOWED_FIELDS = {
    "chapter_url", "title_key", "title", "chapter", "chapter_num", "source",
    "cover", "series_url", "updated_time", "release_date", "origin",
    "description", "type", "genres", "rating",
}


def normalize_recent_chapter_rows(
    rows: list[dict],
    wl_origins: dict | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Validate and coerce collector rows into the shape the table expects.

    Returns (cleaned, skipped_by_source). Every field a collector emits is
    either carried through or replaced by a typed default -- never silently
    dropped.

    `origin` is written from the normalized value rather than row["origin"],
    because the raw field holds "manhwa"/"KR" depending on the source while
    the column only accepts KR/CN/JP. Getting this wrong writes NULL and the
    country flag disappears from the feed with no error anywhere.

    Pure on purpose: no database access, so it can be asserted directly.
    """
    allowed = ALLOWED_FIELDS
    wl_origins = wl_origins or {}
    cleaned = []
    _skipped_invalid: dict[str, int] = {}
    for row in rows:
        if not row.get("chapter_url"):
            continue
        # Validate release_date — fallback to updated_time if invalid, else aggregate skip
        _rd = row.get("release_date")
        _valid = False
        if isinstance(_rd, str) and _rd.strip():
            try:
                datetime.fromisoformat(_rd.replace("Z", "+00:00"))
                _valid = True
            except (ValueError, TypeError):
                _valid = False
        if not _valid:
            _ut = row.get("updated_time")
            if isinstance(_ut, str) and _ut.strip():
                try:
                    datetime.fromisoformat(_ut.replace("Z", "+00:00"))
                    row["release_date"] = _ut
                    _valid = True
                except (ValueError, TypeError):
                    pass
        if not _valid:
            _src = row.get("source") or "unknown"
            _skipped_invalid[_src] = _skipped_invalid.get(_src, 0) + 1
            continue
        _raw_origin = row.get("origin") or row.get("type") or ""
        _src = row.get("source") or ""
        _norm = normalize_origin(_raw_origin)
        if not _norm and _src:
            _norm = normalize_origin(_src)
        if not _norm:
            _norm = ""
        _tk_override = str(row.get("title_key") or "").strip()
        if _tk_override and (_tk_override, _src) in wl_origins:
            _norm = wl_origins[(_tk_override, _src)]
        if not row.get("type") and _norm:
            _origin_to_type = {"KR": "manhwa", "CN": "manhua", "JP": "manga"}
            row["type"] = _origin_to_type.get(_norm, "")
        # origin is dropped from the row dict and re-added here: _norm is the
        # only normalized value, and using row["origin"] would write the raw
        # manhwa/KR string straight into a column constrained to KR/CN/JP.
        _r = {k: row.get(k) for k in allowed if k != "origin"}
        _r["origin"] = _norm or None
        for _k in ("rating", "description", "type"):
            if _r.get(_k) is None:
                _r[_k] = ""
        if _r.get("genres") is None:
            _r["genres"] = []
        for _k in ("chapter_num", "rating"):
            _v = _r.get(_k)
            if _v is None or _v == "":
                _r[_k] = 0.0
            else:
                try:
                    _r[_k] = float(_v)
                except (TypeError, ValueError):
                    _r[_k] = 0.0
        _r["chapter_url"] = row["chapter_url"]
        cleaned.append(_r)
    return cleaned, _skipped_invalid


def batch_insert_recent_chapters(rows: list[dict]) -> dict[str, int]:
    if not rows:
        return {"inserted": 0, "failed": 0, "deduped": 0}
    inserted = failed = 0
    _wl_origins_local = _get_wl_origins()
    cleaned, _skipped_invalid = normalize_recent_chapter_rows(rows, _wl_origins_local)
    if _skipped_invalid:
        logger.info("batch_insert: skipped invalid release_date aggregated", skipped=_skipped_invalid, total_skipped=sum(_skipped_invalid.values()), total_rows=len(rows))
    to_upsert: list[dict] = []
    try:
        _need_cover = [r for r in cleaned if not r.get("cover")]
        if _need_cover:
            _tks = list({(r.get("title_key",""), r.get("source","")) for r in _need_cover if r.get("title_key")})
            if _tks:
                _covers = {}
                for i in range(0, len(_tks), 100):
                    chunk = _tks[i:i+100]
                    _sb = get_supabase()
                    try:
                        for src in set(s for _, s in chunk):
                            _src_tks = [tk for tk, s in chunk if s == src]
                            _res = _sb.table("series_meta").select("title_key,source,cover").in_("title_key", _src_tks).eq("source", src).limit(len(_src_tks)*2).execute()
                            for row in _res.data or []:
                                _covers[(row["title_key"], src)] = row.get("cover")
                    except Exception as _e:
                        logger.warn("cover backfill query failed — chapter inserted without cover, retry next cron", err=str(_e)[:160], srcs=list(set(s for _, s in chunk)))
                        try:
                            from modules.manhwa.metrics_prometheus import COVER_BACKFILL_ERRORS
                            for s in set(s for _, s in chunk):
                                COVER_BACKFILL_ERRORS.labels(source=s).inc()
                        except Exception:
                            pass
                for r in cleaned:
                    if not r.get("cover"):
                        r["cover"] = _covers.get((r.get("title_key",""), r.get("source","")), "")
                        if r["cover"] is None:
                            r["cover"] = ""
        _seen_url: set[str] = set()
        _seen_ch: set[tuple[str, str, str]] = set()
        _uniq: list[dict] = []
        for r in cleaned:
            u = r.get("chapter_url")
            if u in _seen_url:
                continue
            _ck = _composite_key(r)
            if _ck and _ck in _seen_ch:
                continue
            _seen_url.add(u)
            if _ck:
                _seen_ch.add(_ck)
            _uniq.append(r)
        to_upsert = _uniq
        if to_upsert:
            existing_urls, existing_ch = _load_existing_rc(to_upsert)
            new_rows: list[dict] = []
            touch_rows: list[dict] = []
            for r in to_upsert:
                if r["chapter_url"] in existing_urls:
                    r["scan_status"] = "updated"
                    r["scan_reason"] = "metadata refreshed"
                    r["confidence_score"] = 100
                    touch_rows.append(r)
                elif _composite_key(r) in existing_ch:
                    continue
                else:
                    r["scan_status"] = "new"
                    r["scan_reason"] = "new chapter"
                    r["confidence_score"] = 100
                    new_rows.append(r)
            CHUNK = 50
            for i in range(0, len(new_rows), CHUNK):
                chunk_rows = new_rows[i : i + CHUNK]
                _retry = 0
                while _retry < 2:
                    try:
                        from modules.manhwa.db_adapter import get_pool_stats as _gps
                        _ps_before = _gps()
                        logger.debug("batchInsert chunk start", chunk=f"{i//CHUNK}", rows=len(chunk_rows), pool=_ps_before, retry=_retry)
                        get_supabase().table("recent_chapters").upsert(chunk_rows, on_conflict="chapter_url").execute()
                        inserted += len(chunk_rows)
                        logger.debug("batchInsert chunk ok", chunk=f"{i//CHUNK}", inserted=inserted)
                        break
                    except Exception as e:
                        from modules.manhwa.db_adapter import get_pool_stats as _gps2
                        _ps_after = _gps2()
                        msg = str(e).lower()
                        if "rc_composite" in msg or "idx_recent_chapters_composite_unique" in msg or "composite_unique" in msg:
                            logger.info("batchInsert race rc_composite benign, skip", chunk=f"{i//CHUNK}", pool=_ps_after)
                            break
                        if ("already closed" in msg or "pool closed" in msg) and _retry == 0:
                            logger.debug("batchInsert pool closed retry", chunk=f"{i//CHUNK}", pool=_ps_after)
                            import time as _t
                            _t.sleep(0.5)
                            _retry += 1
                            continue
                        failed += len(chunk_rows)
                        logger.error("batchInsertRecentChapters chunk failed — data loss", exc=e, exc_info=True, range=f"{i}-{i+len(chunk_rows)}", constraint="title_key,source,chapter_num", first_keys=list(chunk_rows[0].keys()) if chunk_rows else [], first_url=str(chunk_rows[0].get("chapter_url") or "")[:120] if chunk_rows else "", pool=_ps_after)
                        break
            if touch_rows:
                _touch_rows = []
                _skipped_touch: dict[str, int] = {}
                for r in touch_rows:
                    _rd_touch = r.get("release_date")
                    _valid_touch = False
                    if isinstance(_rd_touch, str) and _rd_touch.strip():
                        try:
                            datetime.fromisoformat(_rd_touch.replace("Z", "+00:00"))
                            _valid_touch = True
                        except (ValueError, TypeError):
                            pass
                    if not _valid_touch:
                        _ut_touch = r.get("updated_time")
                        if isinstance(_ut_touch, str) and _ut_touch.strip():
                            try:
                                datetime.fromisoformat(_ut_touch.replace("Z", "+00:00"))
                                r["release_date"] = _ut_touch
                                _valid_touch = True
                            except (ValueError, TypeError):
                                pass
                    if not _valid_touch:
                        _src_t = r.get("source") or "unknown"
                        _skipped_touch[_src_t] = _skipped_touch.get(_src_t, 0) + 1
                        continue
                    _t = {"chapter_url": r["chapter_url"],"scan_status": "updated","scan_reason": "metadata refreshed","confidence_score": 100}
                    for k in ("title_key", "title", "chapter", "chapter_num", "source", "cover", "series_url", "origin", "description", "rating", "genres", "type", "release_date"):
                        v = r.get(k)
                        if v not in (None, "", []):
                            _t[k] = v
                    _touch_rows.append(_t)
                if _skipped_touch:
                    logger.info("batch_insert touch: skipped invalid release_date aggregated", skipped=_skipped_touch)
                _sig_groups: dict[frozenset, list[dict]] = {}
                for r in _touch_rows:
                    _sig_groups.setdefault(frozenset(r.keys()), []).append(r)
                _uniform_chunks = [rows[i : i + CHUNK] for rows in _sig_groups.values() for i in range(0, len(rows), CHUNK)]
                for chunk_rows in _uniform_chunks:
                    _t_retry = 0
                    while _t_retry < 2:
                        try:
                            get_supabase().table("recent_chapters").upsert(chunk_rows, on_conflict="chapter_url").execute()
                            break
                        except Exception as e:
                            msg = str(e).lower()
                            if ("already closed" in msg or "pool closed" in msg) and _t_retry == 0:
                                try:
                                    from modules.manhwa.db_adapter import get_pool_stats as _gps3
                                    _ps_touch = _gps3()
                                except Exception:
                                    _ps_touch = {}
                                logger.debug("batchInsert touch pool closed retry", pool=_ps_touch)
                                import time as _t
                                _t.sleep(0.5)
                                _t_retry += 1
                                continue
                            failed += len(chunk_rows)
                            logger.error("batchInsertRecentChapters touch chunk failed — data loss", exc=e, exc_info=True, range=f"{len(chunk_rows)} rows", constraint="chapter_url", first_keys=list(chunk_rows[0].keys()) if chunk_rows else [], first_url=str(chunk_rows[0].get("chapter_url") or "")[:120] if chunk_rows else "")
                            break
            if len(to_upsert) < len(cleaned):
                logger.info("batchInsertRecentChapters dedup", before=len(rows), after=len(to_upsert))
            if len(new_rows) < len(to_upsert):
                logger.info("batchInsertRecentChapters re-touch/dupe skipped", total=len(to_upsert), inserted=inserted)
        else:
            logger.info("batchInsertRecentChapters: nothing to upsert")
        try:
            existing_rows: list[dict] = []
            all_urls = [r["chapter_url"] for r in cleaned if r.get("chapter_url")]
            for i in range(0, len(all_urls), 100):
                chunk_urls = all_urls[i : i + 100]
                try:
                    res = get_supabase().table("recent_chapters").select("chapter_url, origin").in_("chapter_url", chunk_urls).execute()
                    existing_rows.extend(res.data or [])
                except Exception as e:
                    logger.error("origin backfill lookup chunk failed", exc=e, exc_info=True)
            incoming_origin: dict[str, str] = {}
            incoming_title_key: dict[str, str] = {}
            for d in cleaned:
                if d.get("origin"):
                    incoming_origin[d["chapter_url"]] = d["origin"]
                if d.get("title_key"):
                    incoming_title_key[d["chapter_url"]] = d["title_key"]
            updates = [{"chapter_url": er["chapter_url"],"origin": incoming_origin[er["chapter_url"]],"title_key": incoming_title_key.get(er["chapter_url"]) or er.get("title_key") or ""} for er in existing_rows if not er.get("origin") and er.get("chapter_url") in incoming_origin]
            CHUNK = 50
            for i in range(0, len(updates), CHUNK):
                chunk_rows = updates[i : i + CHUNK]
                if chunk_rows:
                    get_supabase().table("recent_chapters").upsert(chunk_rows, on_conflict="chapter_url").execute()
        except Exception as e:
            logger.error("batchInsertRecentChapters backfill failed", exc=e, exc_info=True)
            failed += len(updates) if 'updates' in locals() else 0
    except Exception as e:
        logger.error("batchInsertRecentChapters failed", exc=e, exc_info=True, constraint="chapter_url", first_keys=list(cleaned[0].keys()) if cleaned else [])
        failed = len(cleaned) if 'cleaned' in locals() else 0
    finally:
        if failed:
            logger.error("batchInsert partial_success", inserted=inserted, failed=failed, total=len(rows))
    return {"inserted": inserted, "failed": failed, "deduped": len(rows) - len(to_upsert) if 'to_upsert' in locals() else 0}
