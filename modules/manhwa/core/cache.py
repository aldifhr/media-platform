"""Unified in-memory cache helper.

Provides a single TTL-cache primitive so every module invalidates the same way.
Replaces ad-hoc _CACHE/_CACHE_TS/_CACHE_TTL dicts spread across the codebase.

Usage:
    from modules.manhwa.core.cache import ttl_cache

    @ttl_cache(ttl=30.0, maxsize=200)
    def load_whitelist(force: bool = False) -> list[dict]:
        ...

    # Invalidate manually when data changes:
    load_whitelist.invalidate()
"""

from __future__ import annotations

import time
from collections import OrderedDict
from functools import wraps
from threading import Lock
from typing import Any, Callable

_REGISTRY: list[Callable] = []

def _make_key(args: tuple, kwargs: dict) -> str:
    try:
        return str(hash((str(args), str(sorted(kwargs.items())))))
    except Exception:
        return str(hash(repr((args, tuple(sorted(kwargs.items()))))))

def ttl_cache(ttl: float = 30.0, maxsize: int = 200):
    """Decorator factory: TTL in-memory cache.

    The wrapped function gains an ``invalidate()`` method.
    ``force=True`` kwarg always bypasses the cache.
    Cache key now correctly includes *args and **kwargs (except `force`).
    """
    def decorator(fn: Callable) -> Callable:
        import inspect
        _has_force = "force" in inspect.signature(fn).parameters
        _cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        _lock = Lock()

        @wraps(fn)
        def wrapper(*args, force: bool = False, **kwargs):
            # force bypass
            if force:
                if _has_force:
                    return fn(*args, force=force, **kwargs)
                return fn(*args, **kwargs)
            key = _make_key(args, kwargs)
            now = time.monotonic()
            with _lock:
                if key in _cache:
                    ts, val = _cache[key]
                    if now - ts < ttl:
                        # move to end (LRU)
                        _cache.move_to_end(key)
                        return val
            # Compute outside lock to avoid blocking readers.
            if _has_force:
                result = fn(*args, force=force, **kwargs)
            else:
                result = fn(*args, **kwargs)
            with _lock:
                _cache[key] = (now, result)
                _cache.move_to_end(key)
                while len(_cache) > maxsize:
                    _cache.popitem(last=False)
            return result

        def invalidate(*_a, **_kw):
            with _lock:
                if not _a and not _kw:
                    _cache.clear()
                else:
                    k = _make_key(_a, _kw)
                    _cache.pop(k, None)

        wrapper.invalidate = invalidate  # type: ignore[attr-defined]
        wrapper._cache = _cache  # type: ignore[attr-defined]
        _REGISTRY.append(wrapper)
        return wrapper
    return decorator

def invalidate_all_caches() -> None:
    """Invalidate every ttl_cache-decorated function via registry (no dir() scan)."""
    for fn in list(_REGISTRY):
        try:
            fn.invalidate()  # type: ignore[attr-defined]
        except Exception:
            pass
