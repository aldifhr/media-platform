"""Tiny in-memory sliding-window rate limiter.

Process-local (like the login limiter in app/api/auth.py): resets on restart
and is not shared across workers. That is acceptable for a single-process
uvicorn deploy and strictly better than no limit on the public image proxy.

Run: PYTHONPATH=. python3 tests/test_rate_limit.py
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, max_hits: int, window_s: int):
        self.max_hits = max_hits
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_s
        with self._lock:
            q = self._hits[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_hits:
                return False
            q.append(now)
            # bound memory: drop empty buckets occasionally
            if len(self._hits) > 10_000:
                for k in [k for k, v in self._hits.items() if not v]:
                    self._hits.pop(k, None)
            return True


# Public image proxy: 120 image fetches / 60s per client IP. Generous enough
# for a reader scrolling a chapter, tight enough to stop amplification.
proxy_limiter = SlidingWindowLimiter(max_hits=120, window_s=60)


def client_ip(request) -> str:
    """Best-effort client IP. Honours the first X-Forwarded-For hop."""
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    except Exception:
        pass
    try:
        return request.client.host or "unknown"
    except Exception:
        return "unknown"
