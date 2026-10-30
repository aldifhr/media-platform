"""Unified media-scrapers entrypoint.

Single FastAPI app (MEDIA_PORT, default 3200). Mounts the pahe router under
``/pahe/*`` for backwards compatibility with callers that still hit the old
port-3100 paths, and exposes a unified ``/check`` that runs the same
:func:`pahe.scanner.service.run_check` the pahe router calls — one
implementation, one lock, one freshness rule.

The scheduler is started from the lifespan: there is no external cron, so
without it the process would serve requests and never scrape anything.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _path in (_ROOT, os.path.join(_ROOT, "pahe"), os.path.join(_ROOT, "pahe", "scanner")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from modules.media.common.auth import check as authorized  # noqa: E402
from modules.media.common.env import env_int, load_env  # noqa: E402
from modules.media.common.log import get_logger  # noqa: E402

load_env()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import modules.media.source_health as sh  # noqa: E402
from modules.media.sources.pahe.scanner import scraper  # noqa: E402
from modules.media.sources.pahe.scanner.main import router as pahe_router  # noqa: E402
from modules.media.sources.pahe.scanner.service import run_check  # noqa: E402
from modules.media.scheduler import start_scheduler, stop_scheduler  # noqa: E402

log = get_logger("media")

API_TOKEN_ENV = "MEDIA_API_TOKEN"
PORT = env_int("MEDIA_PORT", 3200)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # The scheduler is what actually scrapes. Started here because there is
    # no external cron hitting /check — without it the API idles forever.
    start_scheduler()
    try:
        yield
    finally:
        await stop_scheduler()
        # Release the shared httpx client's connection pool on shutdown.
        await scraper.aclose_http()


app = FastAPI(
    title="media-scrapers",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=_lifespan,
)

# Mount pahe router under /pahe prefix (keeps legacy /pahe/check etc working)
app.include_router(pahe_router, prefix="/pahe")


@app.get("/")
async def root():
    """Same payload the standalone pahe router returns, so external callers
    hitting the domain root see no change now that one process serves both."""
    return {
        "name": "pahe-scanner",
        "version": "4.0.0",
        "endpoints": {"/check": "Scrape + notify", "/health": "liveness"},
    }


@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "media-scrapers",
        "sources": ["pahe"],
        "source_health": sh.snapshot(),
    }


@app.get("/check")
async def check(request: Request):
    if not authorized(request.headers, request.query_params, API_TOKEN_ENV):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Unauthorized"})
    result = await run_check(
        dry_run=request.query_params.get("dry") == "1",
        seed=request.query_params.get("seed") == "1",
        recheck=request.query_params.get("recheck") == "1",
    )
    status = result["status"]
    body = json.loads(result["body"])
    return JSONResponse(status_code=status if status >= 400 else 200, content=body)


if __name__ == "__main__":
    import uvicorn

    log.info("starting media-scrapers on port %s", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
