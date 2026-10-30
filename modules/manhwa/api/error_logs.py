"""Logs errors API — GET/DELETE /api/v1/logs/errors."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.manhwa.logger import get_logger
from modules.manhwa.utils.request_auth import require_monitor_auth, int_safe, safe_error
from modules.manhwa.utils.responses import ok, unauthorized

logger = get_logger("api:logs")
router = APIRouter()


@router.get("/logs/errors")
async def list_errors(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        page = int_safe(request.query_params.get("page", "1"), 1)
        page_size = int_safe(request.query_params.get("page_size", "50"), 50, max_val=200)
        level = request.query_params.get("level")
        source = request.query_params.get("source")
        q = request.query_params.get("q")
        from modules.manhwa.storage.error_logs import query_errors
        data = query_errors(page=page, page_size=page_size, level=level, source=source, q=q)
        return ok(data)
    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)


@router.delete("/logs/errors")
async def clear_errors(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    try:
        from modules.manhwa.storage.error_logs import clear_all
        deleted = clear_all()
        return ok({"deleted": deleted})
    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)
