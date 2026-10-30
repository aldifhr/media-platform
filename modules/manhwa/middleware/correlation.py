"""Correlation ID middleware — sets X-Request-Id -> logger cid.

Pure ASGI (not BaseHTTPMiddleware): BaseHTTPMiddleware wraps the request in a
_CachedRequest whose receive races the body read against a disconnect event.
Under uvloop that race can resolve to http.disconnect, so downstream handlers
read an empty body and every POST body fails to parse. A raw ASGI middleware
never touches the receive stream, so the body is delivered intact.
"""
from __future__ import annotations

import uuid

from modules.manhwa.logger import set_correlation_id


class CorrelationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        cid = (
            headers.get(b"x-request-id")
            or headers.get(b"x-correlation-id")
            or uuid.uuid4().hex[:16].encode()
        )
        cid_str = cid.decode("latin1") if isinstance(cid, bytes) else str(cid)
        set_correlation_id(cid_str)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                raw = list(message.get("headers") or [])
                raw.append((b"x-request-id", cid_str.encode("latin1")))
                message = {**message, "headers": raw}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def correlation_middleware(app):
    return CorrelationMiddleware(app)
