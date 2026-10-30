"""Security headers middleware — pure ASGI.

See correlation.py for why this is not BaseHTTPMiddleware.
"""
from __future__ import annotations

_SECURITY_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"no-referrer",
    b"permissions-policy": b"geolocation=(), microphone=(), camera=(), payment=()",
    b"content-security-policy": (
        b"default-src 'self'; "
        b"img-src 'self' data: https:; "
        b"connect-src 'self' https://scanner.aldifhr.my.id https://fe.aldifhr.fun; "
        b"style-src 'self' 'unsafe-inline'; "
        b"script-src 'self' 'unsafe-inline'; "
        b"frame-ancestors 'none'; "
        b"base-uri 'self'"
    ),
    b"x-frame-options": b"DENY",
}


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                raw = list(message.get("headers") or [])
                present = {k.lower() for k, _ in raw}
                for k, v in _SECURITY_HEADERS.items():
                    if k not in present:
                        raw.append((k, v))
                message = {**message, "headers": raw}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def security_headers_middleware(app):
    return SecurityHeadersMiddleware(app)
