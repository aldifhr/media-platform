"""CSRF middleware — enforce double-submit token on mutating routes. Pure ASGI.

Whitelisted paths (no CSRF check): /api/v1/auth, /api/v1/interactive.
Bearer auth bypasses CSRF (API-to-API).

All other mutating routes (POST/PUT/PATCH/DELETE) require:
    x-csrf-token header == manhwa_csrf_token cookie

Pure ASGI (not BaseHTTPMiddleware): the latter's _CachedRequest races the body
read against a disconnect under uvloop, breaking POST body parsing. This
middleware never reads the body, so it must not wrap the receive stream.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("csrf")

_WHITELISTED_PATHS = {
    "/api/v1/auth",
    "/api/v1/interactive",
}

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _headers_dict(scope) -> dict[bytes, bytes]:
    return {k.lower(): v for k, v in (scope.get("headers") or [])}


def _read_csrf_cookie(raw_cookie: str) -> str:
    # Support both current and legacy cookie names
    for name in ("manhwa_csrf_token", "ikiru_csrf_token"):
        marker = f"{name}="
        for part in raw_cookie.split(";"):
            part = part.strip()
            if part.startswith(marker):
                return part[len(marker):]
    return ""


class CsrfMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        if method not in _MUTATING_METHODS:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _WHITELISTED_PATHS:
            await self.app(scope, receive, send)
            return

        headers = _headers_dict(scope)

        auth_header = (headers.get(b"authorization") or b"").decode("latin1")
        if auth_header.lower().startswith("bearer "):
            await self.app(scope, receive, send)
            return

        cookie_header = (headers.get(b"cookie") or b"").decode("latin1")
        csrf_cookie = _read_csrf_cookie(cookie_header)
        csrf_header = (headers.get(b"x-csrf-token") or b"").decode("latin1")

        if not csrf_cookie or not csrf_header:
            logger.warning("csrf: missing cookie or header path=%s method=%s", path, method)
            await self._reject(send, "csrf token missing")
            return

        if csrf_cookie != csrf_header:
            logger.warning("csrf: mismatch path=%s method=%s", path, method)
            await self._reject(send, "csrf token mismatch")
            return

        await self.app(scope, receive, send)

    async def _reject(self, send, message: str):
        payload = json.dumps({"success": False, "error": message}).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ]
        await send({"type": "http.response.start", "status": 403, "headers": headers})
        await send({"type": "http.response.body", "body": payload})


def csrf_middleware(app):
    return CsrfMiddleware(app)
