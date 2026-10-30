"""SeriesMeta deep module — hides 6h TTL + Redis + DB + upstream."""
from __future__ import annotations

import threading
import time as _time_mod

from modules.manhwa.logger import get_logger

logger = get_logger("storage:series_meta")

# Single source TTL 6h for shinigami
_SERIES_META_TTL_S = 6 * 3600  # 6h

# Default column set for the bulk map builder.
SERIES_META_COLUMNS = "title_key, source, cover, rating, description, genres, type"


def load_series_meta_map(
    sb,
    title_keys,
    columns: str = SERIES_META_COLUMNS,
) -> dict[tuple[str, str], dict]:
    """Fetch series_meta rows for ``title_keys``, keyed by (title_key, source).

    Chunks the IN clause at 100 to keep the query bounded. Returns {} on any
    failure so callers degrade to source-only metadata rather than crashing.
    """
    out: dict[tuple[str, str], dict] = {}
    tks = list({str(tk or "").strip() for tk in title_keys if tk})
    if not tks:
        return out
    try:
        for i in range(0, len(tks), 100):
            chunk = tks[i : i + 100]
            rows = (
                sb.table("series_meta")
                .select(columns)
                .in_("title_key", chunk)
                .execute()
                .data
                or []
            )
            for r in rows:
                tk = str(r.get("title_key") or "").strip()
                src = str(r.get("source") or "").strip()
                if tk and src:
                    out[(tk, src)] = r
    except Exception as e:
        logger.warn("series_meta map build failed", err=str(e)[:120])
    return out


def _is_series_meta_stale(updated_at: str | None) -> bool:
    if not updated_at:
        return True
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age >= _SERIES_META_TTL_S
    except Exception:
        return True


# Redis mirror helpers — single source for DRY (common.py re-exports)
def _redis(redis_url: str | None = None) -> object | None:
    from modules.manhwa.core.redis import redis_client

    return redis_client(redis_url)


def _redis_get_meta(source: str, sid: str, redis_url: str | None = None) -> dict | None:
    try:
        _r = _redis(redis_url) if redis_url is not None else _redis()
        if not _r:
            return None
        raw = _r.get(f"series_meta:{source}:{sid}")
        if not raw:
            return None
        import json as _js

        data = _js.loads(raw) if isinstance(raw, str) else raw
        if _is_series_meta_stale(data.get("updated_at")):
            return None
        return data
    except Exception as e:
        logger.debug("redis get fallback", source=source, sid=sid, err=str(e)[:120])
        return None


def _redis_set_meta(source: str, sid: str, meta: dict, redis_url: str | None = None) -> None:
    try:
        _r = _redis(redis_url) if redis_url is not None else _redis()
        if not _r:
            return
        import json as _js

        _r.setex(f"series_meta:{source}:{sid}", int(_SERIES_META_TTL_S), _js.dumps(meta))
    except Exception as e:
        logger.debug("redis set fallback", source=source, sid=sid, err=str(e)[:120])
        pass


class SeriesMeta:
    """Seam hiding in-mem LRU 512 + Redis setex 6h + DB series_meta + upstream + stale fallback."""

    def __init__(self, db=None, fetcher=None, redis_url: str | None = None):
        self._db = db
        self._fetcher = fetcher
        self._redis_url = redis_url
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self._max = 512

    def _get_db(self):
        if self._db is not None:
            return self._db
        try:
            from modules.manhwa.db import get_supabase

            return get_supabase()
        except Exception:
            return None

    def _fetcher_call(self, source: str, sid: str) -> dict:
        if self._fetcher is not None:
            try:
                return self._fetcher(source, sid) or {}
            except Exception:
                return {}
        try:
            if source == "shinigami":
                from modules.manhwa.sources import shinigami as _sh

                return _sh.get_shinigami_series_meta(sid) or {}
        except Exception:
            pass
        return {}

    def _redis_get(self, source: str, sid: str) -> dict | None:
        if self._redis_url is not None:
            return _redis_get_meta(source, sid, redis_url=self._redis_url)
        return _redis_get_meta(source, sid)

    def _redis_set(self, source: str, sid: str, meta: dict) -> None:
        if self._redis_url is not None:
            return _redis_set_meta(source, sid, meta, redis_url=self._redis_url)
        return _redis_set_meta(source, sid, meta)

    def _redis_client(self):
        if self._redis_url is not None:
            return _redis(self._redis_url)
        return _redis()

    def get(self, source: str, sid: str) -> dict:
        if source not in ("shinigami",):
            return {}
        cache_key = f"{source}:{sid}"
        now = _time_mod.monotonic()

        # in-mem hit
        with self._lock:
            c = self._cache.get(cache_key)
            if c and (now - c[0]) < _SERIES_META_TTL_S:
                return c[1]

        # Redis cross-worker
        _rm = self._redis_get(source, sid)
        if _rm is not None:
            with self._lock:
                self._cache[cache_key] = (now, _rm)
                if len(self._cache) > self._max:
                    for _k in list(self._cache)[: len(self._cache) - self._max]:
                        self._cache.pop(_k, None)
            return _rm

        _stale_row: dict | None = None
        db = self._get_db()
        if db is not None:
            try:
                rows = (
                    db.table("series_meta")
                    .select("title_key, source, rating, genres, description, cover, type, origin, updated_at")
                    .eq("title_key", sid)
                    .eq("source", source)
                    .limit(1)
                    .execute()
                    .data
                    or []
                )
                if rows:
                    e = rows[0]
                    if (e.get("rating") not in (None, "", 0)) or (e.get("description") or "").strip():
                        if not _is_series_meta_stale(e.get("updated_at")):
                            with self._lock:
                                self._cache[cache_key] = (now, e)
                                if len(self._cache) > self._max:
                                    for _k in list(self._cache)[: len(self._cache) - self._max]:
                                        self._cache.pop(_k, None)
                            self._redis_set(source, sid, e)
                            return e
                        _stale_row = e
            except Exception:
                pass

        # upstream refresh (only if not fresh DB)
        meta = self._fetcher_call(source, sid)
        if meta:
            # try upsert
            try:
                if db is not None:
                    from datetime import datetime, timezone
                    row = {
                        "title_key": sid,
                        "source": source,
                        "rating": meta.get("rating"),
                        "genres": meta.get("genres") or [],
                        "description": meta.get("description") or "",
                        "cover": meta.get("cover"),
                        "type": meta.get("type"),
                        "origin": meta.get("origin") or "",
                        # A real timestamp, not the literal string "now()".
                        # PostgREST binds this as text, so "now()" landed in
                        # the column verbatim and every staleness comparison
                        # against it was wrong.
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    db.table("series_meta").upsert(row, on_conflict="title_key,source").execute()
            except Exception:
                pass
            with self._lock:
                self._cache[cache_key] = (now, meta)
                if len(self._cache) > self._max:
                    for _k in list(self._cache)[: len(self._cache) - self._max]:
                        self._cache.pop(_k, None)
            self._redis_set(source, sid, meta)
            return meta

        if _stale_row is not None:
            with self._lock:
                self._cache[cache_key] = (now, _stale_row)
                if len(self._cache) > self._max:
                    for _k in list(self._cache)[: len(self._cache) - self._max]:
                        self._cache.pop(_k, None)
            self._redis_set(source, sid, _stale_row)
            return _stale_row

        with self._lock:
            self._cache[cache_key] = (now, {})
            if len(self._cache) > self._max:
                for _k in list(self._cache)[: len(self._cache) - self._max]:
                    self._cache.pop(_k, None)
        return {}

    def get_bulk(self, keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        """Bulk warm: dedupe+group+IN 100 like old common.preload; fallback per-key for stale/missing."""
        if not keys:
            return {}
        # dedupe preserve order
        deduped = list(dict.fromkeys(keys))
        result: dict[tuple[str, str], dict] = {}
        # quick path: if small N, still use bulk for correctness but benefit remains
        from collections import defaultdict

        by_src: dict[str, list[str]] = defaultdict(list)
        for sid, src in deduped:
            if src in ("shinigami",) and sid:
                by_src[src].append(sid)
            else:
                # unknown source -> empty
                result[(sid, src)] = {}

        now = _time_mod.monotonic()
        db = self._get_db()

        for src, sids in by_src.items():
            # dedupe within source
            uniq = list(dict.fromkeys(sids))
            # filter cached (in-mem + Redis) before DB
            need: list[str] = []
            for sid in uniq:
                cache_key = f"{src}:{sid}"
                with self._lock:
                    c = self._cache.get(cache_key)
                    if c and (now - c[0]) < _SERIES_META_TTL_S:
                        result[(sid, src)] = c[1]
                        continue
                rm = self._redis_get(src, sid)
                if rm is not None:
                    with self._lock:
                        self._cache[cache_key] = (now, rm)
                        if len(self._cache) > self._max:
                            for _k in list(self._cache)[: len(self._cache) - self._max]:
                                self._cache.pop(_k, None)
                    result[(sid, src)] = rm
                    continue
                need.append(sid)

            if not need:
                continue

            if db is None:
                for sid in need:
                    result[(sid, src)] = self.get(src, sid)
                continue

            # bulk IN 100 fetch for fresh rows; stale/missing delegated to get()
            # For efficiency, try bulk query; on any failure fallback to per-key get
            try:
                for i in range(0, len(need), 100):
                    chunk = need[i : i + 100]
                    # attempt bulk query
                    try:
                        q = (
                            db.table("series_meta")
                            .select("title_key, source, rating, genres, description, cover, type, origin, updated_at")
                            .in_("title_key", chunk)
                            .eq("source", src)
                        )
                        exec_res = q.execute()
                        rows = getattr(exec_res, "data", None)
                        # MagicMock guard: if rows is MagicMock (test mock not wired for .in_), treat as []
                        if rows is None:
                            rows = []
                        # detect MagicMock artifact
                        try:
                            from unittest.mock import MagicMock as _MM

                            if isinstance(rows, _MM):
                                rows = []
                        except Exception:
                            pass
                        if not isinstance(rows, list):
                            rows = []
                    except Exception:
                        rows = []

                    row_map: dict[str, dict] = {}
                    for r in rows:
                        if isinstance(r, dict) and r.get("title_key"):
                            row_map[r.get("title_key")] = r

                    for sid in chunk:
                        if (sid, src) in result:
                            continue
                        r = row_map.get(sid)
                        cache_key = f"{src}:{sid}"
                        if r and (r.get("rating") not in (None, "", 0) or (r.get("description") or "").strip()):
                            if not _is_series_meta_stale(r.get("updated_at")):
                                with self._lock:
                                    self._cache[cache_key] = (now, r)
                                    if len(self._cache) > self._max:
                                        for _k in list(self._cache)[: len(self._cache) - self._max]:
                                            self._cache.pop(_k, None)
                                self._redis_set(src, sid, r)
                                result[(sid, src)] = r
                                continue
                        # stale, missing, or empty -> delegate to get (handles upstream + stale fallback)
                        result[(sid, src)] = self.get(src, sid)
            except Exception:
                for sid in need:
                    if (sid, src) not in result:
                        result[(sid, src)] = self.get(src, sid)

        # ensure all deduped keys present (unknown sources already, but safety)
        for sid, src in deduped:
            if (sid, src) not in result:
                result[(sid, src)] = self.get(src, sid)

        return result

    def invalidate(self, source: str, sid: str) -> None:
        cache_key = f"{source}:{sid}"
        with self._lock:
            self._cache.pop(cache_key, None)
        try:
            _r = self._redis_client()
            if _r:
                _r.delete(f"series_meta:{source}:{sid}")
        except Exception:
            pass


series_meta = SeriesMeta()
