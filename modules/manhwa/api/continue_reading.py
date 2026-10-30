"""Continue reading — per-device sync via session_hash."""
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.manhwa.logger import get_logger
from modules.manhwa.utils.request_auth import require_monitor_auth
from modules.manhwa.utils.responses import err, ok, unauthorized

logger = get_logger("api:continue-reading")
router = APIRouter()

# 20 entries of reading progress is a few KB. The cap exists because the
# endpoint writes straight to the DB: without one, any authenticated session
# could park an arbitrarily large blob in continue_reading.entries.
_MAX_BODY_BYTES = 64 * 1024
_MAX_ENTRIES_BYTES = 32 * 1024


def _too_large(request: Request) -> bool:
    """Reject an oversized body before it is buffered."""
    clen = request.headers.get("content-length") or ""
    if clen.isdigit() and int(clen) > _MAX_BODY_BYTES:
        return True
    return False

def _session_hash(request: Request) -> str | None:
    # Use the session JWT as device session identifier (same as FE continueReading)
    from modules.manhwa.utils.cookies import read_session_cookie

    cookie = read_session_cookie(request.cookies)
    if not cookie:
        # fallback to Authorization Bearer token if present
        auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            cookie = auth[7:].strip()
    if not cookie or len(cookie) < 5:
        return None
    # Reject unvalidated tokens: only accept a real dashboard session JWT.
    # This blocks the previous behavior where any 5+ char string was accepted
    # and keyed progress on the raw hash, causing resets on every refresh.
    try:
        from modules.manhwa.api.auth import role_from_jwt
        if not role_from_jwt(cookie):
            return None
    except Exception:
        return None
    return hashlib.sha256(cookie.encode()).hexdigest()[:16]


@router.get("/continue-reading")
async def get_continue_reading(request: Request):
    h = _session_hash(request)
    if not h:
        return ok({"entries": {}})
    try:
        from modules.manhwa.db import get_supabase
        sb = get_supabase()
        res = sb.table("continue_reading").select("entries, updated_at").eq("session_hash", h).limit(1).execute()
        if res.data:
            row = res.data[0]
            entries = row.get("entries") or {}
            # entries is jsonb, may be string
            if isinstance(entries, str):
                import json as _j
                try:
                    entries = _j.loads(entries)
                except Exception:
                    entries = {}
            return ok({"entries": entries, "updated_at": row.get("updated_at")})
        return ok({"entries": {}})
    except Exception as e:
        logger.warn("continue-reading get failed", err=str(e)[:120])
        return ok({"entries": {}})


@router.put("/continue-reading")
async def put_continue_reading(request: Request):
    h = _session_hash(request)
    if not h:
        return unauthorized()
    if _too_large(request):
        return err("payload too large", status_code=413)
    try:
        _raw = await request.body()
        if len(_raw) > _MAX_BODY_BYTES:
            return err("payload too large", status_code=413)
        import json as _json
        body = _json.loads(_raw)
        entries = body.get("entries") or body.get("data") or {}
        if not isinstance(entries, dict):
            return err("invalid entries", status_code=400)
        if len(_json.dumps(entries, default=str)) > _MAX_ENTRIES_BYTES:
            return err("entries too large", status_code=413)
        # cap 20 entries server-side too
        if len(entries) > 20:
            # keep most recent 20 by updatedAt
            sorted_entries = sorted(entries.items(), key=lambda kv: kv[1].get("updatedAt", "") if isinstance(kv[1], dict) else "", reverse=True)[:20]
            entries = dict(sorted_entries)
        from modules.manhwa.db import get_supabase
        sb = get_supabase()
        # upsert
        sb.table("continue_reading").upsert({"session_hash": h, "entries": entries, "updated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="session_hash").execute()
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.warn("continue-reading put failed", err=str(e)[:120])
        return err("internal error", status_code=500)


@router.delete("/continue-reading")
async def delete_continue_reading(request: Request):
    """Delete continue-reading entries (single or all) for current user."""
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        from modules.manhwa.db import get_supabase
        from modules.manhwa.utils.request_auth import get_session_hash
        session_hash = get_session_hash(request)
        if not session_hash:
            return err("no session", status_code=400)
        
        try:
            body = await request.json()
        except Exception:
            body = {}
        
        title_key = body.get("title_key", "").strip()
        
        sb = get_supabase()
        
        if title_key:
            # Single: fetch, remove key, upsert back
            res = (
                sb.table("continue_reading").select("entries")
                .eq("session_hash", session_hash).maybe_single().execute()
            )
            entries = {}
            if res.data and isinstance(res.data.get("entries"), dict):
                entries = res.data["entries"]
            entries.pop(title_key, None)
            sb.table("continue_reading").upsert(
                {"session_hash": session_hash, "entries": entries, "updated_at": datetime.now(timezone.utc).isoformat()},
                on_conflict="session_hash"
            ).execute()
        else:
            # All: delete row
            sb.table("continue_reading").delete().eq("session_hash", session_hash).execute()
        
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.warn("delete_continue_reading failed", err=str(e)[:200])
        return err("internal error", status_code=500)


@router.post("/continue-reading/bulk")
async def bulk_update_continue_reading(request: Request):
    """Bulk update continue-reading entries (full map) for current user."""
    if not require_monitor_auth(request):
        return unauthorized()
    if _too_large(request):
        return err("payload too large", status_code=413)
    try:
        _raw = await request.body()
        if len(_raw) > _MAX_BODY_BYTES:
            return err("payload too large", status_code=413)
        import json as _json
        body = _json.loads(_raw)
    except Exception:
        return err("invalid JSON", status_code=400)
    entries = body.get("entries", {})
    if not isinstance(entries, dict):
        return err("entries must be object", status_code=400)
    if len(_json.dumps(entries, default=str)) > _MAX_ENTRIES_BYTES:
        return err("entries too large", status_code=413)
    try:
        from modules.manhwa.db import get_supabase
        from modules.manhwa.utils.request_auth import get_session_hash
        session_hash = get_session_hash(request)
        if not session_hash:
            return err("no session", status_code=400)
        get_supabase().table("continue_reading").upsert(
            {"session_hash": session_hash, "entries": entries, "updated_at": datetime.now(timezone.utc).isoformat()},
            on_conflict="session_hash",
        ).execute()
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.warn("bulk_update_continue_reading failed", err=str(e)[:200])
        return err("internal error", status_code=500)
