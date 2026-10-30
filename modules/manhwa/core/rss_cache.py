"""RSS response cache — shared state + invalidation.

Lives in core so both the API layer (which reads/writes the cache) and the
storage layer (which invalidates it on whitelist changes) can use it
without a layering violation.
"""
from __future__ import annotations

import time as _time

_RSS_CACHE: dict[str, tuple[float, dict]] = {}
_RSS_TTL = 30.0

_rss_new_cache: dict[str, tuple[float, dict]] = {}


def rss_cache_get(key: str):
    entry = _RSS_CACHE.get(key)
    if entry and (_time.monotonic() - entry[0]) < _RSS_TTL:
        return entry[1]
    return None


def rss_cache_put(key: str, val: dict):
    _RSS_CACHE[key] = (_time.monotonic(), val)
    if len(_RSS_CACHE) > 200:
        oldest = sorted(_RSS_CACHE.items(), key=lambda kv: kv[1][0])[:50]
        for k, _ in oldest:
            _RSS_CACHE.pop(k, None)


def invalidate_rss_cache(key_prefix: str | None = None):
    """Invalidate RSS cache — per-key if prefix given, else all.

    Excluded whitelist changes affect all RSS keys (filter applied in Python),
    so caller may clear all. If prefix given, only matching keys are removed
    to avoid thundering herd on unrelated queries.
    """
    if key_prefix is None:
        _RSS_CACHE.clear()
    else:
        for k in list(_RSS_CACHE.keys()):
            if k.startswith(key_prefix):
                _RSS_CACHE.pop(k, None)
