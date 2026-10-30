"""FastAPI router for pahe-scanner.

Endpoints:
  GET /check   - scrape pahe.ink, notify new/updated posts (token-gated)
  GET /test    - send one test notification (token-gated)
  GET /health  - liveness + stats (open)
  GET /status  - is a check running? (open)
  GET /reset   - force-clear the lock if stuck (token-gated)

The check itself lives in :mod:`pahe.scanner.service` so that this router and
``media.py`` run exactly the same code with exactly the same lock.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.media.common.auth import check as authorized
from modules.media.common.env import load_env
from modules.media.common.log import get_logger
from modules.media.sources.pahe.scanner import scraper
from modules.media.sources.pahe.scanner.notify import send_notification
from modules.media.sources.pahe.scanner.service import SEEN, run_check

load_env()

log = get_logger("pahe.router")

router = APIRouter()

_STARTED = time.time()


def _uptime() -> float:
    """Seconds since this module was imported (process start, effectively)."""
    return time.time() - _STARTED


def _require_token(request: Request) -> bool:
    return authorized(request.headers, request.query_params, "PAHE_API_TOKEN")


@router.get("/check")
async def check(request: Request):
    if not _require_token(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Unauthorized"})
    result = await run_check(
        dry_run=request.query_params.get("dry") == "1",
        seed=request.query_params.get("seed") == "1",
        recheck=request.query_params.get("recheck") == "1",
    )
    status = result["status"]
    body = json.loads(result["body"])
    return JSONResponse(status_code=status if status >= 400 else 200, content=body)


@router.get("/test")
async def test(request: Request):
    if not _require_token(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Unauthorized"})
    try:
        posts = await scraper.scrape_latest_posts([])
        if not posts:
            return JSONResponse(content={"ok": False, "error": "No posts found"})
        post = posts[0]
        ok = await send_notification(post)
        return JSONResponse(
            content={"ok": ok, "post": post.title, "downloads": len(post.downloads)}
        )
    except Exception as exc:
        log.exception("test notification failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "error": "Internal error"})


@router.get("/health")
async def health():
    return {"ok": True, "uptime": _uptime(), "seen": SEEN.count()}


@router.get("/status")
async def status():
    from modules.media.sources.pahe.scanner.service import _lock

    return {"ok": True, "checking": _lock().locked(), "uptime": _uptime()}


@router.get("/reset")
async def reset(request: Request):
    """Release the check lock without waiting for the running check to finish.

    The lock is per-event-loop and reentrant-free, so there is nothing to
    force-clear: a stuck check holds it until it returns or the process dies.
    Kept for compatibility with existing callers; it now reports state instead
    of mutating a global.
    """
    if not _require_token(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Unauthorized"})
    from modules.media.sources.pahe.scanner.service import _lock

    return {"ok": True, "checking": _lock().locked()}


@router.get("/")
async def root():
    return {
        "name": "pahe-scanner",
        "version": "4.0.0",
        "endpoints": {"/check": "Scrape + notify", "/health": "liveness"},
    }
