"""Pahe.ink release-notifier service (FastAPI).

Runs the pahe.scanner router on PAHE_PORT (default 3100). nginx proxies
all-scan.aldifhr.my.id -> 127.0.0.1:3100.

Uses shared common.discord / common.state / common.env. Config comes from the
repository-root ``.env``.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (
    os.path.dirname(os.path.abspath(__file__)),
    _ROOT,
    os.path.join(_ROOT, "pahe", "scanner"),
):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from modules.media.common.env import env_int, load_env  # noqa: E402

load_env()

from fastapi import FastAPI  # noqa: E402

from modules.media.sources.pahe.scanner import scraper  # noqa: E402
from modules.media.sources.pahe.scanner.main import router as pahe_router  # noqa: E402


@asynccontextmanager
async def _lifespan(_app):
    yield
    # Release the shared httpx client's connection pool on shutdown.
    await scraper.aclose_http()


app = FastAPI(title="pahe-scanner", version="4.0.0", lifespan=_lifespan)
app.include_router(pahe_router)


if __name__ == "__main__":
    import uvicorn

    port = env_int("PAHE_PORT", env_int("PORT", 3100))
    uvicorn.run(app, host="0.0.0.0", port=port)
