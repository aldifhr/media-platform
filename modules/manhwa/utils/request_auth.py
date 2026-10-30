"""Shared request-auth + query-param helpers for API routers."""
from typing import Any

from fastapi import Request
from modules.manhwa.utils.auth import check_monitor_auth
from modules.manhwa.utils.cookies import read_session_cookie


def require_monitor_auth(request: Request) -> bool:
    return check_monitor_auth(
        request.headers.get("authorization", ""),
        request.query_params.get("token", ""),
        cookie=read_session_cookie(request.cookies),
    )


def get_session_hash(request: Request) -> str | None:
    """Return a stable per-session hash for continue-reading storage.
    
    Uses the session cookie (JWT) — sha256[:16].
    Falls back to Authorization Bearer <REDACTED>
    """
    import hashlib as _hashlib
    
    cookie = read_session_cookie(request.cookies)
    if cookie:
        return _hashlib.sha256(cookie.encode()).hexdigest()[:16]
    
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token and token != "null":
            return _hashlib.sha256(token.encode()).hexdigest()[:16]
    
    return None


def require_cron_auth(request: Request) -> bool:
    from modules.manhwa.utils.auth import check_cron_auth

    # Support both ?token= and ?key= (FastCron uses ?key=) — both deprecated, Bearer preferred
    token_q = request.query_params.get("token", "") or request.query_params.get("key", "")
    return check_cron_auth(
        token_q,
        authorization=request.headers.get("authorization", ""),
    )



def int_safe(value: str | None, default: int = 0, max_val: int | None = None) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if max_val is not None and n > max_val:
        return max_val
    if n < 0:
        return default
    return n


_ERROR_TEXT_KEYS = frozenset({"lastError", "last_error"})


def scrub_upstream_errors(node: Any, *, authenticated: bool) -> Any:
    """Null raw upstream error text in a payload for anonymous callers.

    `last_error` holds whatever the scraper caught — hostnames, full URLs,
    occasionally upstream config. The status page and the home-page
    announcement card are public, so an anonymous caller must not read it;
    an authenticated operator still gets the text needed to debug.
    """
    if authenticated:
        return node
    if isinstance(node, dict):
        return {
            k: (None if k in _ERROR_TEXT_KEYS else scrub_upstream_errors(v, authenticated=False))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [scrub_upstream_errors(v, authenticated=False) for v in node]
    return node


def safe_error(e: Exception, message: str = "internal server error") -> dict:
    try:
        from modules.manhwa.metrics import inc

        inc("errors_500")
    except Exception:
        pass
    return {"success": False, "error": message}


def validation_422(ve):
    """JSON-safe 422 response for a pydantic ValidationError.

    `ve.errors()` embeds the raised exception under ctx.error for custom
    validators ({"ctx": {"error": ValueError(...)}}). ValueError is not JSON
    serializable, so json.dumps raises inside JSONResponse and every validation
    failure becomes a 500 instead of a 422. Stringify anything exception-like.
    """
    from fastapi.responses import JSONResponse

    details = []
    for err in ve.errors():
        e = dict(err)
        ctx = e.get("ctx")
        if isinstance(ctx, dict):
            e["ctx"] = {
                k: (str(v) if isinstance(v, BaseException) else v)
                for k, v in ctx.items()
            }
        details.append(e)
    return JSONResponse(
        content={"success": False, "error": "validation_error", "details": details},
        status_code=422,
    )
