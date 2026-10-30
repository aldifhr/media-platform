"""Auth helpers for monitor/cron endpoints — single password model."""

from __future__ import annotations

import hmac

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger

logger = get_logger("utils:auth")


def _is_production() -> bool:
    """True when running in production. Query-param auth is refused there."""
    try:
        return str(getattr(settings, "ENVIRONMENT", "") or "").lower() == "production"
    except Exception:
        return True  # fail closed: unknown env -> treat as production


def _dashboard_passwords() -> list[str]:
    return [c for c in (settings.DASHBOARD_PASSWORD,) if c]

def token_matches(provided: str, *, role: str = "both") -> bool:
    if not provided or not isinstance(provided, str):
        return False
    provided = provided.strip()
    if not provided:
        return False
    if role == "cron":
        candidates = [c for c in (settings.CRON_SECRET,) if c]
    elif role == "monitor":
        candidates = _dashboard_passwords()
    else:  # both — legacy
        candidates = [c for c in (settings.CRON_SECRET, *_dashboard_passwords()) if c]
    if not candidates:
        return False
    return any(hmac.compare_digest(provided, str(c)) for c in candidates)

def cron_token_matches(provided: str) -> bool:
    return token_matches(provided, role="cron")

def monitor_token_matches(provided: str) -> bool:
    return token_matches(provided, role="monitor")

def check_monitor_auth(authorization: str = "", token_param: str = "", cookie: str = "") -> bool:
    if getattr(settings, "AUTH_DISABLED", False):
        if getattr(settings, "ENVIRONMENT", "production").lower() == "production":
            logger.warn("AUTH_DISABLED=true ignored in production (auth enforced)")
        else:
            return True
    if not _dashboard_passwords():
        return False
    if authorization and authorization.lower().startswith("bearer "):
        if monitor_token_matches(authorization[7:].strip()):
            return True
    if token_param and monitor_token_matches(token_param):
        # DEPRECATED: ?token= leaks to logs/history/analytics — use Authorization: Bearer *** cookie (removal 2026-12-09)
        if not _is_production():
            logger.warn("DEPRECATED ?token= used for monitor auth → use Authorization: Bearer *** session cookie")
            return True
    if cookie:
        import jwt as _jwt

        try:
            secret = settings.AUTH_SECRET
            if not secret:
                return False
            _jwt.decode(cookie, secret, algorithms=["HS256"])
            return True
        except Exception:
            return False
    return False

def check_cron_auth(token_param: str = "", authorization: str = "") -> bool:
    if getattr(settings, "AUTH_DISABLED", False):
        if getattr(settings, "ENVIRONMENT", "production").lower() == "production":
            logger.warn("AUTH_DISABLED=true ignored in production (auth enforced)")
        else:
            return True
    if not settings.CRON_SECRET:
        return False
    # Canonical: Authorization: Bearer <CRON_SECRET> — preferred (no log leakage)
    if authorization and authorization.lower().startswith("bearer "):
        if cron_token_matches(authorization[7:].strip()):
            return True
    if token_param and cron_token_matches(token_param):
        # DEPRECATED: ?token= leaks to logs/history — use Authorization: Bearer *** 2026-12-09)
        if not _is_production():
            logger.warn("DEPRECATED ?token= used for cron auth → use Authorization: ***")
            return True
    return False
