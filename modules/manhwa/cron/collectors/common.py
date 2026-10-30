"""Shared caches + helpers for per-source collectors."""
from __future__ import annotations

import re
import time as _time_mod
import threading

from modules.manhwa.core.rating_utils import normalize_rating
from modules.manhwa.logger import get_logger

logger = get_logger("cron:collect:common")

# Canonical chapter-item schema shared by every collector. Keeping the key
# set in one place stops the per-source dicts from drifting (a missing key
# silently drops a field downstream).
ITEM_FIELDS = (
    "title",
    "title_key",
    "chapter",
    "chapter_num",
    "url",
    "source",
    "cover",
    "series_url",
    "chapter_url",
    "origin",
    "updated_time",
    "release_date",
    "rating",
    "genres",
    "description",
    "type",
)


def build_item(**fields) -> dict:
    """Build a canonical chapter item with every ITEM_FIELDS key present.

    Unknown keys are ignored; omitted keys default to None so downstream
    ``.get`` calls behave the same across sources.
    """
    return {k: fields.get(k) for k in ITEM_FIELDS}


def apply_series_meta_fallback(
    *,
    rating,
    description,
    genres,
    content_type,
    meta_item: dict | None,
) -> tuple:
    """Gap-fill rating/description/genres/type from a series_meta row.

    Only fills fields that are still empty — a value already scraped from the
    source always wins. Returns the (rating, description, genres, content_type)
    tuple so callers can unpack in place.
    """
    if not isinstance(meta_item, dict):
        return rating, description, genres, content_type
    if not rating and meta_item.get("rating") not in (None, ""):
        rating = normalize_rating(meta_item.get("rating")) or 0.0
    if not description and meta_item.get("description"):
        description = str(meta_item.get("description") or "").strip()
    if not genres and meta_item.get("genres"):
        genres = list(meta_item.get("genres") or [])
    if not content_type and meta_item.get("type"):
        content_type = str(meta_item.get("type") or "").lower()
    return rating, description, genres, content_type


def whitelisted_for_source(source: str) -> list[dict]:
    """Whitelist rows for one source, straight from the storage layer.

    Shared by collectors that need to know which series are subscribed for
    their source. Returns raw rows; callers project the fields they need.
    """
    try:
        from modules.manhwa.db import get_supabase

        return (
            get_supabase()
            .table("whitelist")
            .select("title_key, series_url, title")
            .eq("source", source)
            .execute()
            .data
            or []
        )
    except Exception as e:
        logger.warn("whitelist read failed", source=source, err=str(e)[:120])
        return []


def _origin_to_type(origin: str) -> str:
    o = (origin or "").upper()
    if o == "KR":
        return "manhwa"
    if o == "CN":
        return "manhua"
    if o == "JP":
        return "manga"
    return ""

MAX_CHAPTERS_PER_SERIES = 25

_CHAPTER_CACHE: dict[str, tuple[float, list]] = {}
_CHAPTER_CACHE_TTL = 300.0
_CHAPTER_CACHE_MAX = 512

_CHAPTER_CACHE_LOCK = threading.Lock()

_PARSE_TYPES_CACHE: dict[str, list[str]] = {}
_PARSE_TYPES_CACHE_MAX = 1024

_COLLECT_WORKERS = 12
_SOURCE_TIMEOUT = 120.0

def _cached_chapter_list(source: str, sid: str, fetcher) -> list:
    key = f"{source}:{sid}"
    with _CHAPTER_CACHE_LOCK:
        cached = _CHAPTER_CACHE.get(key)
        if cached and (_time_mod.monotonic() - cached[0]) < _CHAPTER_CACHE_TTL:
            return cached[1]
    try:
        data = fetcher() or []
    except Exception as e:
        err_str = str(e).lower()
        if "429" in err_str or "500" in err_str or "503" in err_str:
            _time_mod.sleep(0.5)
            data = fetcher() or []
        else:
            data = []
    with _CHAPTER_CACHE_LOCK:
        _CHAPTER_CACHE[key] = (_time_mod.monotonic(), data)
        if len(_CHAPTER_CACHE) > _CHAPTER_CACHE_MAX:
            for _k in list(_CHAPTER_CACHE)[:len(_CHAPTER_CACHE) - _CHAPTER_CACHE_MAX]:
                _CHAPTER_CACHE.pop(_k, None)
    return data

def _parse_types(raw) -> list[str]:
    import ast
    cache_key = (str(raw) if raw is not None else "")
    try:
        return _PARSE_TYPES_CACHE[cache_key]
    except KeyError:
        pass
    if raw is None:
        result = []
    elif isinstance(raw, list):
        result = [str(x).lower() for x in raw if x]
    else:
        s = str(raw).strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, (list, tuple)):
                    result = [str(x).lower() for x in parsed if x]
                else:
                    result = []
            except Exception:
                result = []
        else:
            result = [p.strip().lower() for p in re.split(r"[,\s]+", s) if p.strip()]
    _PARSE_TYPES_CACHE[cache_key] = result
    if len(_PARSE_TYPES_CACHE) > _PARSE_TYPES_CACHE_MAX:
        for _k in list(_PARSE_TYPES_CACHE)[:len(_PARSE_TYPES_CACHE) - _PARSE_TYPES_CACHE_MAX]:
            _PARSE_TYPES_CACHE.pop(_k, None)
    return result
