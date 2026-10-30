"""Caches for RecentChapters — WL origin + existing_rc."""
import hashlib
import threading
import time as _t
from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger
from modules.manhwa.core.time import RECENT_WINDOW_HOURS, cutoff_iso

logger = get_logger("storage:recent-chapters:caches")

_wl_lock = threading.Lock()
_existing_rc_lock = threading.Lock()

_EXISTING_RC_CACHE: dict[str, tuple[set[str], set[tuple[str, str, str]], float]] = {}
_EXISTING_RC_TTL = 60.0

_WL_ORIGINS: dict[tuple[str, str], str] = {}
_WL_ORIGIN_TTL = 600.0
_WL_ORIGIN_TS = 0.0


def _norm_chapter_num(v) -> str | None:
    try:
        return ("%.10g" % float(v))
    except (ValueError, TypeError):
        return None


def _composite_key(r: dict) -> tuple[str, str, str] | None:
    """(title_key, source, chapter_num) identity, or None when incomplete.

    Single implementation shared by recent_chapters_sql (fetch) and
    recent_chapters_ingest (insert) so both agree on the "oneshot" fallback.
    """
    tk = r.get("title_key") or ""
    src = r.get("source") or ""
    cn = _norm_chapter_num(r.get("chapter_num"))
    if not tk or not src:
        return None
    if cn is None:
        return (tk, src, "oneshot")
    return (tk, src, cn)


def _load_existing_rc(rows: list[dict]) -> tuple[set[str], set[tuple[str, str, str]]]:
    existing_urls: set[str] = set()
    existing_ch: set[tuple[str, str, str]] = set()
    tks = sorted({(r.get("title_key") or "") for r in rows if r.get("title_key")})
    if not tks:
        return existing_urls, existing_ch
    _key = hashlib.sha256("\x00".join(tks).encode()).hexdigest()
    with _existing_rc_lock:
        _cached = _EXISTING_RC_CACHE.get(_key)
        if _cached and (_t.time() - _cached[2]) < _EXISTING_RC_TTL:
            return _cached[0], _cached[1]
    _cutoff = cutoff_iso(RECENT_WINDOW_HOURS)
    # A failed lookup chunk must not discard the whole batch: the caller's
    # upsert is idempotent on chapter_url, so a row missed here is merely
    # re-upserted, never duplicated. Re-raising (the old behaviour) made one
    # bad chunk mark every chapter in the batch as failed and inserted none.
    for i in range(0, len(tks), 100):
        chunk = tks[i:i + 100]
        try:
            res = (
                get_supabase()
                .table("recent_chapters")
                .select("chapter_url, title_key, source, chapter_num")
                .in_("title_key", chunk)
                .gte("updated_time", _cutoff)
                .execute()
            )
            for er in (res.data or []):
                u = er.get("chapter_url") or ""
                if u:
                    existing_urls.add(u)
                _tk = er.get("title_key") or ""
                _src = er.get("source") or ""
                _cn = _norm_chapter_num(er.get("chapter_num"))
                if _tk and _src and _cn:
                    existing_ch.add((_tk, _src, _cn))
        except Exception as e:
            logger.error(
                "existing_rc lookup chunk failed — continuing with partial results",
                exc=e,
                exc_info=True,
                chunk_index=i,
                chunk_keys=len(chunk),
                first_key=chunk[0] if chunk else "",
            )
            continue
    try:
        with _existing_rc_lock:
            _EXISTING_RC_CACHE[_key] = (existing_urls, existing_ch, _t.time())
            if len(_EXISTING_RC_CACHE) > 64:
                _EXISTING_RC_CACHE.pop(next(iter(_EXISTING_RC_CACHE)))
    except Exception:
        pass
    return existing_urls, existing_ch


def _get_wl_origins(force: bool = False) -> dict[tuple[str, str], str]:
    global _WL_ORIGINS, _WL_ORIGIN_TS
    with _wl_lock:
        if not force and _WL_ORIGINS and (_t.time() - _WL_ORIGIN_TS) < _WL_ORIGIN_TTL:
            return _WL_ORIGINS
        try:
            from modules.manhwa.db import get_supabase as _gsb_wl
            _sb_wl = _gsb_wl()
            _wl_rows = _sb_wl.table("series_meta").select("title_key,source,origin").neq("origin", "").execute().data or []
            _new_origins = {}
            for _wl in _wl_rows:
                _tk_wl = str(_wl.get("title_key") or "").strip()
                _src_wl = str(_wl.get("source") or "").strip()
                _orig_wl = str(_wl.get("origin") or "").strip().upper()
                if _tk_wl and _src_wl and _orig_wl:
                    _new_origins[(_tk_wl, _src_wl)] = _orig_wl
            _WL_ORIGINS = _new_origins
            _WL_ORIGIN_TS = _t.time()
        except Exception as _e:
            logger.warn("series_meta origin refresh failed — using stale cache", err=str(_e)[:160])
        return _WL_ORIGINS


def invalidate_whitelist_origin_cache() -> None:
    global _WL_ORIGINS, _WL_ORIGIN_TS
    with _wl_lock:
        _WL_ORIGINS = {}
        _WL_ORIGIN_TS = 0.0
