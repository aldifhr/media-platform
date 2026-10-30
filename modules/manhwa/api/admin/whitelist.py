"""Auto-split from dashboard.py — whitelist routes."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal, Optional

from modules.manhwa.logger import get_logger
from modules.manhwa.services.whitelist_service import (
    get_dispatch_history,
    get_whitelist,
    post_whitelist,
    delete_whitelist,
    patch_whitelist,
    normalize_whitelist_urls,
)
from modules.manhwa.utils.request_auth import require_monitor_auth, int_safe, safe_error, validation_422
from modules.manhwa.utils.responses import err, unauthorized
from modules.manhwa.services.audit import log_action, AuditAction

logger = get_logger("api:whitelist")
router = APIRouter()


class WhitelistCreate(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    title: str = Field(..., min_length=1, max_length=200)
    # Validated against config.VALID_SOURCES rather than a hardcoded Literal.
    # A source missing from the Literal was rejected with a 422 on POST, so the
    # FE's "Add WL" silently failed for it even though every downstream layer
    # supported it. Reading the config keeps this in step automatically.
    source: str = Field(default="shinigami")

    @field_validator("source")
    @classmethod
    def _known_source(cls, v: str) -> str:
        from modules.manhwa.config import VALID_SOURCES

        val = (v or "").strip().lower()
        if val not in VALID_SOURCES:
            raise ValueError(f"unknown source {v!r}; expected one of {list(VALID_SOURCES)}")
        return val
    title_key: Optional[str] = Field(default=None, max_length=200)
    titleKey: Optional[str] = Field(default=None, max_length=200)
    cover: Optional[str] = Field(default=None, max_length=2000)
    rating: Optional[float | str] = None
    type: Optional[Literal["manhwa", "manhua", "manga"]] = None
    genres: Optional[list[str]] = None
    description: Optional[str] = Field(default=None, max_length=5000)
    url: Optional[str] = Field(default=None, max_length=500)
    seriesUrl: Optional[str] = Field(default=None, max_length=500)
    series_url: Optional[str] = Field(default=None, max_length=500)

class WhitelistPatch(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    title_key: Optional[str] = Field(default=None, max_length=200)
    titleKey: Optional[str] = Field(default=None, max_length=200)
    title: Optional[str] = Field(default=None, max_length=200)
    source: Optional[Literal["shinigami", "ikiru", ""]] = None
    url: Optional[str] = Field(default=None, max_length=500)
    seriesUrl: Optional[str] = Field(default=None, max_length=500)
    series_url: Optional[str] = Field(default=None, max_length=500)
    rating: Optional[float | str] = None
    cover: Optional[str] = Field(default=None, max_length=2000)
    type: Optional[Literal["manhwa", "manhua", "manga"]] = None
    genres: Optional[list[str]] = None
    description: Optional[str] = Field(default=None, max_length=5000)

class WhitelistDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title_key: Optional[str] = Field(default=None, max_length=200)
    titleKey: Optional[str] = Field(default=None, max_length=200)
    title: Optional[str] = Field(default=None, max_length=200)
    source: Optional[str] = Field(default=None, max_length=50)
    id: Optional[str] = Field(default=None, max_length=500)
    url: Optional[str] = Field(default=None, max_length=500)

class WhitelistNormalizeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    dry_run: Optional[bool] = None

@router.get("/dispatch-history")
async def dispatch_history(request: Request):
    """Flat list of all dispatched (notified) chapters from dispatch_history."""
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        page = int_safe(request.query_params.get("page", "1"), 1)
        _ps_raw = request.query_params.get("page_size", "50")
        try:
            page_size = int(_ps_raw)
        except (ValueError, TypeError):
            page_size = 50
        if page_size > 1000 or page_size < 1:
            return err("page_size must be between 1 and 1000", status_code=400)
        search = request.query_params.get("search", "").strip().lower()
        # private session-dependent → never HTTP cache (prevent cross-user leak)
        return JSONResponse(content=get_dispatch_history(page, page_size, search), headers={"Cache-Control": "private, no-store, must-revalidate", "Vary": "Cookie", "Pragma": "no-cache"})
    except Exception as e:
        logger.warn("dispatch-history failed", err=str(e))
        return JSONResponse(content=safe_error(e), status_code=500)

@router.get("/reader/dispatch-history")
async def dispatch_history_reader(request: Request):
    """Alias for FE compatibility — /api/v1/reader/dispatch-history → /api/v1/dispatch-history."""
    return await dispatch_history(request)

@router.get("/reader/whitelist")
async def get_whitelist_reader(request: Request):
    """Backward-compat alias — public GET for anon dashboard, same as /whitelist."""
    if not require_monitor_auth(request):
        return unauthorized()
    page = request.query_params.get("page", "1")
    page_size = request.query_params.get("page_size", request.query_params.get("pageSize", "100"))
    _merge_raw = (request.query_params.get("merge") or "false").lower()
    _merge = _merge_raw not in ("false", "0", "no")
    cursor = request.query_params.get("cursor")
    data = get_whitelist(page=page, page_size=page_size, merge=_merge, cursor=cursor)
    # whitelist is private per-user (auth required) → never allow shared or browser HTTP cache
    if isinstance(data, dict):
        return JSONResponse(content=data, headers={"Cache-Control": "private, no-store, must-revalidate", "Vary": "Cookie", "Pragma": "no-cache"})
    return data

@router.get("/whitelist")
async def whitelist_get(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        source = request.query_params.get("source", "")
        title = request.query_params.get("title") or request.query_params.get("q", "")
        page = request.query_params.get("page", "1")
        page_size = request.query_params.get("page_size", request.query_params.get("pageSize", "100"))
        _merge_raw = (request.query_params.get("merge") or "false").lower()
        _merge = _merge_raw not in ("false", "0", "no")
        cursor = request.query_params.get("cursor")
        result = get_whitelist(source=source, title=title, page=page, page_size=page_size, merge=_merge, cursor=cursor)
        return JSONResponse(content=result, headers={"Cache-Control": "private, no-store, must-revalidate", "Vary": "Cookie", "Pragma": "no-cache"})
    except Exception as e:
        logger.warn("whitelist failed", err=str(e))
        return JSONResponse(content=safe_error(e), status_code=500)

@router.post("/reader/whitelist")
async def whitelist_post_reader(request: Request):
    """Alias for FE compatibility — /api/v1/reader/whitelist → /api/v1/whitelist."""
    return await whitelist_post(request)

@router.post("/whitelist")
async def whitelist_post(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        body = await request.json()
    except Exception:
        return err("invalid JSON body", status_code=400)
    if not isinstance(body, dict):
        return err("body must be an object", status_code=400)
    try:
        data = WhitelistCreate.model_validate(body)
    except Exception as ve:
        from pydantic import ValidationError as _VE
        if isinstance(ve, _VE):
            return validation_422(ve)
        raise
    # normalize aliases: title_key/titleKey -> title_key, seriesUrl/series_url/url -> url
    title = data.title
    url = data.url or data.seriesUrl or data.series_url
    source = data.source
    # build body dict for service (include all validated fields, keep original keys for service compat)
    body_dict = body  # service reads many aliases, keep original but validated
    # also ensure type is passed (previously silent drop)
    res = post_whitelist(title=title, url=url, source=source, body=body_dict)
    status_code = 200 if res.get("status") == "ok" else 500
    try:
        from modules.manhwa.api.rss import invalidate_rss_cache
        invalidate_rss_cache()
    except Exception:
        pass
    try:
        log_action(AuditAction.WHITELIST_ADD, request=request, resource="whitelist", resource_id=title or data.title_key or "", metadata={"source": source, "status": res.get("status")})
    except Exception:
        pass
    try:
        from modules.manhwa.services.dispatch.history import invalidate_dh_cache
        invalidate_dh_cache()
    except Exception:
        pass
    return JSONResponse(content=res, status_code=status_code)

@router.delete("/whitelist")
async def whitelist_delete(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        body = await request.json()
    except Exception:
        return err("invalid JSON body", status_code=400)
    if not isinstance(body, dict):
        return err("body must be an object", status_code=400)
    try:
        validated_del = WhitelistDeleteRequest.model_validate(body)
    except Exception as ve:
        from pydantic import ValidationError as _VE
        if isinstance(ve, _VE):
            return validation_422(ve)
        raise
    # Accept any identifier the FE might send (id/title_key/title/url/source).
    title_key = validated_del.title_key or validated_del.titleKey or ""
    source = validated_del.source or ""
    entry_id = validated_del.id or ""
    title = validated_del.title or ""
    url = validated_del.url or ""
    if not any([title_key, entry_id, title, url]):
        # Fall back to deriving title_key from url/title (legacy behavior).
        if url:
            title_key = url.rstrip("/").split("/")[-1]
        elif title:
            from modules.manhwa.core.text import slugify_title_key
            title_key = slugify_title_key(title)
    if not any([title_key, entry_id, title, url]):
        return JSONResponse(content={"error": "one of title_key/id/title/url required"}, status_code=400)
    try:
        result = delete_whitelist(
            title_key=title_key,
            source=source,
            id=entry_id,
            title=title,
            url=url,
        )
        try:
            from modules.manhwa.api.rss import invalidate_rss_cache
            invalidate_rss_cache()
        except Exception:
            pass
        try:
            from modules.manhwa.services.dispatch.history import invalidate_dh_cache
            invalidate_dh_cache()
        except Exception:
            pass
        try:
            log_action(AuditAction.WHITELIST_DELETE, request=request, resource="whitelist", resource_id=title_key or title or entry_id, metadata={"source": source, "status": result.get("status")})
        except Exception:
            pass
        # Map not_found -> 404 so the FE gets a real signal instead of 200 ok.
        if result.get("status") == "not_found":
            return JSONResponse(content=result, status_code=404)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)

@router.patch("/whitelist")
async def whitelist_patch(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        body = await request.json()
    except Exception:
        return err("invalid JSON body", status_code=400)
    if not isinstance(body, dict):
        return err("body must be an object", status_code=400)
    try:
        data = WhitelistPatch.model_validate(body)
    except Exception as ve:
        from pydantic import ValidationError as _VE2
        if isinstance(ve, _VE2):
            return validation_422(ve)
        raise
    # resolve title_key from aliases
    title_key = data.title_key or data.titleKey or ""
    if not title_key:
        url = data.url or data.seriesUrl or data.series_url or ""
        if url:
            title_key = url.rstrip("/").split("/")[-1]
        elif data.title:
            from modules.manhwa.core.text import slugify_title_key
            title_key = slugify_title_key(data.title)
    if not title_key:
        return err("title_key required", status_code=400)
    source = data.source or ""
    # updatable now includes type (previously silent drop)
    updatable = ("rating", "cover", "origin", "genres", "description", "title", "series_url", "type")
    # map aliases to canonical keys
    alias_map = {"seriesUrl": "series_url", "titleKey": "title_key"}
    body_aliased = {}
    for k, v in body.items():
        body_aliased[alias_map.get(k, k)] = v
    updates = {}
    for f in updatable:
        v = body_aliased.get(f)
        if v is not None and v != "":
            updates[f] = v
    try:
        result = patch_whitelist(title_key, source, updates)
        try:
            log_action(AuditAction.WHITELIST_UPDATE, request=request, resource="whitelist", resource_id=title_key, metadata={"source": source, "updates": list(updates.keys())})
        except Exception:
            pass
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)

@router.post("/reader/whitelist/normalize-urls")
async def whitelist_normalize_urls(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return err("body must be an object", status_code=400)
    try:
        validated_norm = WhitelistNormalizeRequest.model_validate(body)
    except Exception as ve:
        from pydantic import ValidationError as _VE
        if isinstance(ve, _VE):
            return validation_422(ve)
        raise
    dry_run = bool(validated_norm.dry_run)
    res = normalize_whitelist_urls(dry_run=dry_run)
    try:
        if not dry_run:
            log_action(AuditAction.WHITELIST_NORMALIZE, request=request, resource="whitelist", resource_id="normalize-urls", metadata={"result": str(res)[:500]})
    except Exception:
        pass
    return JSONResponse(content=res)