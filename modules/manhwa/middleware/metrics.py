"""Prometheus metrics middleware. Pure ASGI.

See correlation.py for why this is not BaseHTTPMiddleware.
"""
from __future__ import annotations

import time


class MetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.time()
        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)

        path = scope.get("path", "")
        if path not in ("/metrics", "/healthz"):
            try:
                from modules.manhwa.metrics_prometheus import track_request

                track_request(
                    method=scope.get("method", ""),
                    endpoint=path,
                    status=status_holder["status"],
                    duration=time.time() - start,
                )
            except Exception:
                pass


def metrics_middleware(app):
    return MetricsMiddleware(app)
