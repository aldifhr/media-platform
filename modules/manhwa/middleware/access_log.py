"""Access log middleware — real-time request/response logging. Pure ASGI.

See correlation.py for why this is not BaseHTTPMiddleware.
"""
from __future__ import annotations

import time

from modules.manhwa.logger import get_logger

logger = get_logger("http")


class AccessLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        t0 = time.monotonic()
        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            dt = (time.monotonic() - t0) * 1000
            client = scope.get("client")
            logger.info(
                "access",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_holder["status"],
                ms=round(dt, 1),
                ip=client[0] if client else None,
            )


def access_log_middleware(app):
    return AccessLogMiddleware(app)
