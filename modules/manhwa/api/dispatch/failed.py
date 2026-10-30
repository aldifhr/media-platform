"""Failed-dispatches API — list, retry, delete.

Split out of api/dispatch/__init__.py.
"""
import time as _time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional

from modules.manhwa.logger import get_logger
from modules.manhwa.utils.request_auth import require_monitor_auth, int_safe, safe_error, validation_422
from modules.manhwa.utils.responses import err, ok, unauthorized
from modules.manhwa.services.audit import log_action, AuditAction

logger = get_logger("api:dispatches")
router = APIRouter()


class FailedDispatchRetryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Optional[str] = Field(default=None, max_length=500)


class FailedDispatchDeleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapter_url: Optional[str] = Field(default=None, max_length=500)
    id: Optional[str] = Field(default=None, max_length=500)


@router.get("/failed-dispatches")
async def failed_dispatches(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    from modules.manhwa.db import get_supabase

    source = request.query_params.get("source", "")
    limit = int_safe(request.query_params.get("limit", "50"), 50, max_val=1000)
    offset = int_safe(request.query_params.get("offset", "0"), 0, max_val=100000)
    try:
        q = (
            get_supabase()
            .table("failed_dispatches")
            .select("*", count="exact")
            .in_("status", ["pending", "failed"])
            .order("created_at", desc=True)
            .limit(limit)
            .offset(offset)
        )
        if source:
            q = q.eq("source", source)
        res = q.execute()
        rows = res.data or []

        urls = [r["chapter_url"] for r in rows if r.get("chapter_url")]
        rc_by_url = {}
        if urls:
            try:
                rc_res = (
                    get_supabase()
                    .table("recent_chapters")
                    .select("chapter_url, title_key, title, chapter, cover, series_url, source")
                    .in_("chapter_url", urls)
                    .execute()
                )
                for r in (rc_res.data or []):
                    if r.get("chapter_url"):
                        rc_by_url[r["chapter_url"]] = r
            except Exception:
                pass
        from modules.manhwa.storage import whitelist as wl_store
        try:
            wl_rows = wl_store.load_whitelist()
            # Key by (title_key, source): a series can be whitelisted on more
            # than one source, and keying by title_key alone picked an arbitrary
            # one, so the display title could come from the wrong source.
            wl_by_key = {
                (w.get("title_key"), w.get("source")): w for w in wl_rows
            }
        except Exception:
            wl_by_key = {}

        results = []
        for r in rows:
            rc = rc_by_url.get(r.get("chapter_url", ""), {})
            tk = r.get("title_key") or rc.get("title_key") or ""
            src = r.get("source") or rc.get("source") or ""
            wl = wl_by_key.get((tk, src), {})
            title = wl.get("title") or rc.get("title") or tk
            cover = rc.get("cover") or ""
            # Fallback: whitelist cover missing, rc pruned → series_meta
            if not cover:
                try:
                    sm = get_supabase().table("series_meta").select("cover").eq("title_key", tk).eq("source", r.get("source", "")).limit(1).execute()
                    cover = (sm.data or [{}])[0].get("cover", "") or ""
                except Exception:
                    pass
            results.append({
                "id": r.get("chapter_url"),
                "chapterUrl": r.get("chapter_url"),
                "titleKey": tk,
                "title": title,
                "chapter": r.get("chapter_title") or rc.get("chapter") or "",
                "source": r.get("source", ""),
                "cover": cover,
                "seriesUrl": rc.get("series_url", "") or wl.get("series_url", ""),
                "error": r.get("error_message", "") or r.get("error", ""),
                "createdAt": r.get("created_at", ""),
            })

        return JSONResponse(
            content={
                "success": True,
                "data": {
                    "results": results,
                    "total": res.count or len(results),
                    "pageSize": limit,
                    "offset": offset,
                    "totalPages": ((res.count or len(results)) + limit - 1) // limit if limit else 1,
                    "hasMore": (offset + limit) < (res.count or len(results)),
                },
            }
        )
    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)


@router.post("/failed-dispatches")
async def failed_dispatches_action(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    action = request.query_params.get("action", "")
    try:
        raw_body = await request.json() if request.headers.get("content-length") else {}
    except Exception:
        raw_body = {}
    if not isinstance(raw_body, dict):
        return err("body must be an object", status_code=400)
    try:
        validated = FailedDispatchRetryBody.model_validate(raw_body)
    except Exception as ve:
        from pydantic import ValidationError as _VE
        if isinstance(ve, _VE):
            return validation_422(ve)
        raise
    dispatch_id = validated.id
    if not dispatch_id:
        dispatch_id = request.query_params.get("id")
    if action == "retry" and dispatch_id:
        from modules.manhwa.db import get_supabase
        from modules.manhwa.cron.dispatch_mod import dispatch, _load_channels
        sb = get_supabase()
        fd = sb.table("failed_dispatches").select("*").eq("chapter_url", dispatch_id).execute()
        fd_row = (fd.data or [None])[0]
        if not fd_row:
            return err("not found", status_code=404)
        rc = sb.table("recent_chapters").select("*").eq("chapter_url", dispatch_id).limit(1).execute()
        rc_row = (rc.data or [None])[0] or {}
        item = {
            "title": fd_row.get("title") or rc_row.get("title") or fd_row.get("title_key") or "Untitled",
            "title_key": fd_row.get("title_key") or rc_row.get("title_key") or "",
            "chapter": fd_row.get("chapter") or rc_row.get("chapter") or "",
            "chapter_url": dispatch_id,
            "source": fd_row.get("source") or rc_row.get("source") or "",
            "cover": rc_row.get("cover") or fd_row.get("cover") or "",
            "series_url": rc_row.get("series_url") or fd_row.get("series_url") or "",
            "origin": rc_row.get("origin") or "",
        }
        channels = _load_channels()
        try:
            sent = dispatch([item], channels, f"retry-{int(_time.time())}") if channels else 0
        except Exception as e:
            logger.warn("retry dispatch failed", err=str(e)[:200])
            return JSONResponse(content=safe_error(e), status_code=500)
        if sent > 0:
            # dispatch() already wrote the dispatch_history row (by fcfs_key,
            # dedup-safe) — do NOT re-insert here (that would collide on
            # the chapter_url PK and throw, which previously swallowed the
            # next line and left the failed_dispatches row forever-stuck).
            try:
                sb.table("failed_dispatches").delete().eq("chapter_url", dispatch_id).execute()
            except Exception as _e:
                logger.warn("retry: failed_dispatches delete failed", err=str(_e)[:120])
            try:
                log_action(AuditAction.DISPATCH_RETRY, request=request, resource="failed_dispatches", resource_id=dispatch_id, metadata={"sent": sent})
            except Exception:
                pass
            return JSONResponse(content={"success": True, "retried": dispatch_id, "sent": sent})
        return err("dispatch returned 0", status_code=502, retried=dispatch_id)
    if action == "retry-all":
        # Reuse the SAME per-action lock as the cron "update" path so a manual
        # retry-all can never overlap a scheduled update (which would
        # double-send chapters — the bug fixed in dispatch dedupe logic).
        # Run in a daemon thread (fire-and-forget) but guarded by the lock.
        from modules.manhwa.api.system import get_cron_lock
        from modules.manhwa.cron.pipeline import run_pipeline

        lock = get_cron_lock("update")
        if not lock.acquire(blocking=False):
            return err("update already running; retry later", status_code=409)
        try:
            run_pipeline(action="update", do_dispatch=True)
            try:
                log_action(AuditAction.DISPATCH_RETRY_ALL, request=request, resource="dispatch", resource_id="retry-all", metadata={"action": "update"})
            except Exception:
                pass
        except Exception as e:
            logger.warn("retry-all failed", err=str(e)[:200])
            return JSONResponse(content=safe_error(e), status_code=500)
        finally:
            lock.release()
        return ok({"status": "done"})
    return err("unknown action", status_code=400)


@router.delete("/failed-dispatches")
async def failed_dispatches_delete(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    chapter_url = request.query_params.get("id", "") or request.query_params.get("chapter_url", "")
    if not chapter_url:
        try:
            raw_body = await request.json()
            if not isinstance(raw_body, dict):
                return err("body must be an object", status_code=400)
            try:
                validated_del = FailedDispatchDeleteBody.model_validate(raw_body)
            except Exception as ve:
                from pydantic import ValidationError as _VE2
                if isinstance(ve, _VE2):
                    return validation_422(ve)
                raise
            chapter_url = validated_del.chapter_url or validated_del.id or ""
        except Exception as e:
            # keep 400 for missing body vs 422 for validation; re-raise validation already handled
            if "validation_error" in str(e):
                raise
            pass
    from modules.manhwa.db import get_supabase

    if not chapter_url:
        return err("chapter_url or id required", status_code=400)
    try:
        get_supabase().table("failed_dispatches").delete().eq("chapter_url", chapter_url).execute()
        try:
            log_action(AuditAction.QUEUE_CLEAR, request=request, resource="failed_dispatches", resource_id=chapter_url, metadata={"action": "delete"})
        except Exception:
            pass
        return JSONResponse(content={"success": True})
    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)
