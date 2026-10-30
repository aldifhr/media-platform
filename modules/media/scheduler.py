"""In-process scheduler for the live media sources.

The service runs as a single uvicorn process, so the scheduler is an asyncio
task on the same event loop as the FastAPI app — no external cron, no HTTP
self-call. Callers run inline in the same process, which is why pahe's
in-process check lock works across both the scheduler and the /check endpoint.

Started from media.py's lifespan. Without it nothing ever triggers a check:
the API sits idle and no new post is ever scraped.

It used to import ``_run_pahe_check`` out of ``media`` — a circular import
(media imports the scheduler at module level) that only worked because the
import was deferred into the function body. It now calls the service directly,
so the dependency runs one way.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

from modules.media.common.env import env_int, load_env
from modules.media.common.log import get_logger
from modules.media.sources.pahe.scanner.service import run_check

load_env()

log = get_logger("scheduler")

# Interval per source. pahe releases daily-ish, cheap poll.
_SOURCES = ("pahe", "ovagames", "skidrow", "game3rb", "onlinefix", "freetp")
_INTERVAL_S = {
    "pahe": env_int("PAHE_CHECK_INTERVAL_MINUTES", 30) * 60,
    "ovagames": env_int("OVAGAMES_CHECK_INTERVAL_MINUTES", 30) * 60,
    "skidrow": env_int("SKIDROW_CHECK_INTERVAL_MINUTES", 30) * 60,
    "game3rb": env_int("GAME3RB_CHECK_INTERVAL_MINUTES", 30) * 60,
    "onlinefix": env_int("ONLINEFIX_CHECK_INTERVAL_MINUTES", 30) * 60,
    "freetp": env_int("FREETP_CHECK_INTERVAL_MINUTES", 30) * 60,
}
_POLL_S = 30

_task: asyncio.Task | None = None


_NEW_SOURCE_RUNNERS = {
    "ovagames": "modules.media.sources.ovagames.bot",
    "skidrow": "modules.media.sources.skidrow.bot",
    "game3rb": "modules.media.sources.game3rb.bot",
    "onlinefix": "modules.media.sources.onlinefix.bot",
    "freetp": "modules.media.sources.freetp.bot",
}


def _run_new_source(name: str) -> dict:
    """Run a resurrected game source's scrape+notify cycle (sync, blocking)."""
    import importlib
    mod = importlib.import_module(_NEW_SOURCE_RUNNERS[name])
    return mod.run_once(dry_run=False)


async def _tick(name: str) -> None:
    """Run one source's check. Never raises — a failed source must not
    kill the loop, or every other source silently stops too."""
    started = time.time()
    try:
        if name == "pahe":
            result = await run_check(dry_run=False)
            try:
                summary = json.loads(result["body"])
            except (ValueError, TypeError):
                summary = result["body"]
        else:
            summary = await asyncio.to_thread(_run_new_source, name)
    except Exception as exc:
        log.warning("%s crashed: %s: %s", name, type(exc).__name__, exc)
        return
    log.info("%s ok in %.1fs -> %s", name, time.time() - started, summary)


async def _loop() -> None:
    log.info("started, sources=%s, intervals=%s", list(_SOURCES), _INTERVAL_S)
    for index, name in enumerate(_SOURCES):
        if index:
            await asyncio.sleep(10)
        await _tick(name)

    # Measure from completion, not from start: a check that overruns its
    # interval must not fire again immediately afterwards.
    last = {name: time.monotonic() for name in _SOURCES}
    while True:
        await asyncio.sleep(_POLL_S)
        now = time.monotonic()
        for name in _SOURCES:
            if now - last[name] < _INTERVAL_S[name]:
                continue
            last[name] = time.monotonic()
            await _tick(name)


async def _supervised_loop() -> None:
    """``_loop`` wrapped so it can never die.

    An asyncio task that raises is never retried: the exception surfaces only
    when the task is garbage-collected, and the app keeps serving HTTP while
    scraping silently stops forever. That is the one failure mode an external
    cron did not have, so the loop is made unkillable instead — a crash logs
    and the loop restarts.
    """
    while True:
        try:
            await _loop()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # deliberate catch-all: see docstring
            log.exception("scheduler loop died (%s: %s); restarting", type(exc).__name__, exc)
            await asyncio.sleep(5)


def start_scheduler() -> None:
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_supervised_loop(), name="media-scheduler")
    log.info("task created")


async def stop_scheduler() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
    _task = None
    log.info("stopped")
