"""Audit log service — append-only trail for admin mutations.

Table: audit_log (064_audit_log.sql)
Actions: WHITELIST_ADD, WHITELIST_UPDATE, WHITELIST_DELETE, DISPATCH, CRON_TRIGGER, QUEUE_RETRY, QUEUE_CLEAR, etc.

"""
from __future__ import annotations

from typing import Any

from modules.manhwa.logger import get_logger
from modules.manhwa.utils.cookies import read_session_cookie

logger = get_logger("services:audit")

class AuditAction:
    WHITELIST_ADD = "WHITELIST_ADD"
    WHITELIST_UPDATE = "WHITELIST_UPDATE"
    WHITELIST_DELETE = "WHITELIST_DELETE"
    WHITELIST_NORMALIZE = "WHITELIST_NORMALIZE"
    EXCLUDED_ADD = "EXCLUDED_ADD"
    EXCLUDED_DELETE = "EXCLUDED_DELETE"
    EXCLUDED_BULK = "EXCLUDED_BULK"
    DISPATCH = "DISPATCH"
    DISPATCH_RETRY = "DISPATCH_RETRY"
    DISPATCH_RETRY_ALL = "DISPATCH_RETRY_ALL"
    CRON_TRIGGER = "CRON_TRIGGER"
    QUEUE_RETRY = "QUEUE_RETRY"
    QUEUE_CLEAR = "QUEUE_CLEAR"
    SETTINGS_UPDATE = "SETTINGS_UPDATE"
    CONTINUE_READING_PUT = "CONTINUE_READING_PUT"
    CONTINUE_READING_MARK_READ = "CONTINUE_READING_MARK_READ"

def _coerce_inet(value: str) -> str | None:
    """Return a value Postgres `inet` accepts, else None.

    The inet column rejects anything that is not a literal IP. Client-supplied
    input reaches this from x-forwarded-for and client.host, so it can be a
    hostname ("testclient"), an "ip:port" pair, or a bracketed IPv6 literal.
    Normalize those here; return None when there is no usable IP so the caller
    stores NULL instead of aborting the whole audit insert.
    """
    import ipaddress

    raw = (value or "").strip()
    if not raw:
        return None
    # Bracketed IPv6 with port: "[::1]:8080" -> "::1"
    if raw.startswith("["):
        raw = raw[1:].split("]")[0]
    # IPv4 with port: "1.2.3.4:8080" -> "1.2.3.4" (a bare IPv6 has >1 colon)
    if raw.count(":") == 1 and "." in raw:
        raw = raw.split(":")[0]
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def _extract_request_meta(request) -> dict[str, str]:
    """Extract actor/ip/user_agent from Request without raising."""
    try:
        # actor: prefer x-forwarded-for, fallback to client host
        fwd = request.headers.get("x-forwarded-for", "") if hasattr(request, "headers") else ""
        ip = fwd.split(",")[0].strip() if fwd else ""
        if not ip and hasattr(request, "client") and request.client:
            ip = getattr(request.client, "host", "") or ""
        ip = _coerce_inet(ip) or ""
        ua = request.headers.get("user-agent", "")[:500] if hasattr(request, "headers") else ""
        # actor: session hash or Bearer prefix (don't log secret)
        actor = "dashboard"
        auth = request.headers.get("authorization", "") if hasattr(request, "headers") else ""
        if auth.lower().startswith("bearer "):
            actor = "bearer:" + auth[7:].strip()[:8] + "***"
        elif _sess := read_session_cookie(request.cookies):
            actor = "session:" + _sess[:8] + "***"
        return {"ip": ip[:45], "user_agent": ua, "actor": actor[:100]}
    except Exception:
        return {"ip": "", "user_agent": "", "actor": "unknown"}

def log_action(
    action: str,
    request=None,
    resource: str = "",
    resource_id: str = "",
    metadata: dict[str, Any] | None = None,
    actor: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Append audit log row. Never raises — failures are warn-only."""
    try:
        meta_req = _extract_request_meta(request) if request is not None else {}
        final_actor = (actor or meta_req.get("actor") or "unknown")[:100]
        # inet column rejects anything that is not a literal IP (empty string,
        # hostnames, "ip:port") — normalize, else store NULL. An explicit ip=
        # argument is caller-supplied and needs the same treatment.
        final_ip = _coerce_inet(ip if ip is not None else meta_req.get("ip", ""))
        final_ua = (user_agent if user_agent is not None else meta_req.get("user_agent", ""))[:500]
        final_metadata = metadata or {}
        # Ensure JSON serializable, truncate large values
        import json as _json
        try:
            _json.dumps(final_metadata)
        except Exception:
            final_metadata = {"raw": str(final_metadata)[:2000]}

        # Truncate resource fields
        resource = (resource or "")[:100]
        resource_id = (resource_id or "")[:500]
        action = (action or "UNKNOWN")[:50]

        from modules.manhwa.db import get_supabase
        sb = get_supabase()
        # Use raw q for minimal overhead and to avoid builder column allowlist issues
        # Fallback to builder if q not available
        try:
            from modules.manhwa.db import q as _q
            _q(
                "INSERT INTO audit_log (actor, action, resource, resource_id, ip, user_agent, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
                [final_actor, action, resource, resource_id, final_ip, final_ua, _json.dumps(final_metadata)],
            )
        except Exception:
            # Fallback via builder (handles missing table gracefully)
            try:
                sb.table("audit_log").insert({
                    "actor": final_actor,
                    "action": action,
                    "resource": resource,
                    "resource_id": resource_id,
                    "ip": final_ip,
                    "user_agent": final_ua,
                    "metadata": final_metadata,
                }).execute()
            except Exception as e2:
                # Table may not exist yet (migration not run) — log warn, don't block caller
                if "does not exist" in str(e2) or "audit_log" in str(e2).lower():
                    logger.debug("audit_log table missing — skip", action=action)
                else:
                    logger.warn("audit insert failed", action=action, err=str(e2)[:200])
    except Exception as e:
        logger.warn("audit log_action failed", action=action, err=str(e)[:200])

def log_action_sync(*args, **kwargs) -> None:
    """Alias for sync callers that cannot await."""
    log_action(*args, **kwargs)
