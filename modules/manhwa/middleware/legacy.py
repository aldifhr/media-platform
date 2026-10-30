"""Legacy /api -> /api/v1 compat — one prefix rewrite. Pure ASGI.

See correlation.py for why this is not BaseHTTPMiddleware.
"""
from __future__ import annotations

import json


def _json_response(status: int, body: dict) -> tuple[bytes, list]:
    payload = json.dumps(body).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
    ]
    return payload, headers


class LegacyRedirectMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if (
            path.startswith("/api/")
            and not path.startswith("/api/v1/")
            and not path.startswith("/api/docs")
            and not path.startswith("/api/openapi")
        ):
            # 307 redirects bypass CORS; return 404 instead.
            payload, headers = _json_response(
                404, {"success": False, "error": "not found — use /api/v1/..."}
            )
            await send({"type": "http.response.start", "status": 404, "headers": headers})
            await send({"type": "http.response.body", "body": payload})
            return

        await self.app(scope, receive, send)


def legacy_redirect_middleware(app):
    return LegacyRedirectMiddleware(app)
