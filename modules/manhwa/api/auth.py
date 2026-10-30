"""Auth endpoint: POST /api/auth?action=login|refresh.

Single shared password (DASHBOARD_PASSWORD env) — no admin/member roles.
Sets the `manhwa_dashboard_session` JWT cookie used by the FE gate.
"""

from __future__ import annotations

import hmac
from modules.manhwa.utils.request_auth import validation_422
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

import jwt as _jwt

from modules.manhwa.config import settings

class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(..., min_length=1, max_length=200)

router = APIRouter()

from modules.manhwa.utils.cookies import (
    CSRF_COOKIE as _COOKIE_CSRF,
    SESSION_COOKIE as _COOKIE_SESSION,
    _LEGACY_CSRF_COOKIE,
    _LEGACY_ROLE_COOKIE,
)

_COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 days

def _issue_jwt() -> str:
    secret = settings.AUTH_SECRET
    if not secret:
        raise RuntimeError("AUTH_SECRET not configured")
    exp = int(time.time()) + _COOKIE_MAX_AGE
    return _jwt.encode(
        {"sub": "dashboard", "exp": exp, "iat": int(time.time())},
        secret,
        algorithm="HS256",
    )

def role_from_jwt(token: str) -> str | None:
    """Compat shim — always returns 'user' for valid JWT, None otherwise."""
    secret = settings.AUTH_SECRET
    if not secret or not token:
        return None
    try:
        _jwt.decode(token, secret, algorithms=["HS256"])
        return "user"
    except Exception:
        return None

def _get_session_cookie(request: Request) -> str | None:
    cookie = request.cookies.get(_COOKIE_SESSION)
    if cookie:
        return cookie
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None

def _set_session_cookies(resp: JSONResponse, token: str) -> None:
    import secrets as _secrets

    resp.set_cookie(
        key=_COOKIE_SESSION,
        value=token,
        httponly=True,
        secure=True,
        samesite="none",
        domain=".aldifhr.my.id",
        path="/",
        max_age=_COOKIE_MAX_AGE,
    )
    csrf_token = _secrets.token_urlsafe(32)
    resp.set_cookie(
        key=_COOKIE_CSRF,
        value=csrf_token,
        httponly=False,
        secure=True,
        samesite="none",
        domain=".aldifhr.my.id",
        path="/",
        max_age=_COOKIE_MAX_AGE,
    )
    # Clear the pre-rename cookies. They are no longer written, and the
    # client still dual-reads the old session name, so this only drops the
    # csrf and role leftovers that nothing reads any more.
    resp.delete_cookie(key=_LEGACY_CSRF_COOKIE, path="/", domain=".aldifhr.my.id")
    resp.delete_cookie(key=_LEGACY_ROLE_COOKIE, path="/", domain=".aldifhr.my.id")

def _password_ok(pw: str) -> bool:
    return bool(pw and settings.DASHBOARD_PASSWORD and hmac.compare_digest(pw, str(settings.DASHBOARD_PASSWORD)))


# Simple in-memory rate limiter for login: 5 attempts per 300s per client IP.
from collections import defaultdict, deque

_LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW = 300.0


def _check_login_rate(client_ip: str) -> tuple[bool, int | None]:
    now = time.monotonic()
    window = _LOGIN_ATTEMPTS[client_ip]
    while window and now - window[0] > _LOGIN_WINDOW:
        window.popleft()
    if len(window) >= _LOGIN_MAX_ATTEMPTS:
        retry_after = int(_LOGIN_WINDOW - (now - window[0]))
        return False, max(retry_after, 1)
    window.append(now)
    return True, None


def _client_ip(request: Request) -> str:
    # Prefer X-Forwarded-For (Caddy sets this), fall back to direct client host
    fwd = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For") or ""
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")
    return ip or "unknown"


@router.post("/auth")
async def auth_handler(request: Request):
    action = request.query_params.get("action", "login")

    if action == "refresh":
        token = _get_session_cookie(request)
        if not token or not role_from_jwt(token):
            return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)
        new_token = _issue_jwt()
        resp = JSONResponse({"success": True, "data": {"ok": True}})
        _set_session_cookies(resp, new_token)
        return resp

    if action not in ("login",):
        return JSONResponse({"success": False, "error": "Unknown action"}, status_code=400)

    # Rate limit login attempts per IP
    if action == "login":
        allowed, retry_after = _check_login_rate(_client_ip(request))
        if not allowed:
            return JSONResponse(
                {"success": False, "error": "too many attempts", "retry_after": retry_after},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"success": False, "error": "Invalid body"}, status_code=400)
    try:
        data = LoginRequest.model_validate(body)
    except Exception as ve:
        from pydantic import ValidationError as _VE
        if isinstance(ve, _VE):
            return validation_422(ve)
        raise
    password = str(data.password).strip()

    if not password or not _password_ok(password):
        # Record failed attempt
        ip = _client_ip(request)
        if action == "login":
            _LOGIN_ATTEMPTS[ip].append(time.monotonic())
        return JSONResponse({"success": False, "error": "Invalid credentials"}, status_code=401)

    token = _issue_jwt()
    resp = JSONResponse({"success": True, "data": {"ok": True}})
    _set_session_cookies(resp, token)
    return resp

@router.get("/auth")
async def auth_me(request: Request):
    token = _get_session_cookie(request)
    if not token or not role_from_jwt(token):
        return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)
    return JSONResponse({"success": True, "data": {"authenticated": True}})
