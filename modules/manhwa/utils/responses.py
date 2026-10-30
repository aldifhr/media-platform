"""Canonical JSON response envelopes for API routers.

Every endpoint returns one of two shapes:

    {"success": True,  "data": ...}                 # ok()
    {"success": False, "error": "..."}              # err()

Building them by hand at ~60 call sites let the shape drift (e.g. a handler
returning {"error": ...} with no "success" key). Use these helpers instead.
"""
from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def ok(
    data: Any = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    """Success envelope: ``{"success": True, "data": data}``.

    ``extra`` keys are merged into the top-level body (e.g. ``cached=True``).
    ``headers`` sets real HTTP response headers — it must NOT be folded into the
    body, which is what happens if it is only reachable via ``**extra``.
    """
    body: dict[str, Any] = {"success": True, "data": data}
    body.update(extra)
    return JSONResponse(content=body, status_code=status_code, headers=headers)


def err(
    message: str = "internal server error",
    status_code: int = 400,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    """Error envelope: ``{"success": False, "error": message}``."""
    body: dict[str, Any] = {"success": False, "error": message}
    body.update(extra)
    return JSONResponse(content=body, status_code=status_code, headers=headers)


def unauthorized() -> JSONResponse:
    """Canonical 401 body. Pair with ``require_monitor_auth``."""
    return err("unauthorized", status_code=401)
