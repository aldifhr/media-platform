"""Dashboard snapshot builder shared by the admin API and the cron.

Moved out of app/api/admin/stats.py so the route module stays thin; the
payload shape, 15s in-memory cache and error handling are unchanged.
"""

from __future__ import annotations

import asyncio
import time

from modules.manhwa.services.dashboard.inputs import load_inputs, norm_src
from modules.manhwa.services.dashboard.sections import (
    build_chapters_by_source,
    build_cron_averages,
    build_cron_status,
    build_queue_depth,
    build_recent_chapters,
)

# In-memory snapshot cache (15s TTL) — absorbs frontend poll bursts.
_SNAP_CACHE: list = [0.0, None]
_SNAP_TTL = 15.0


async def _build_snapshot() -> dict:
    inp = load_inputs()
    hm = {src: norm_src(src, row) for src, row in (inp.raw_hm or {}).items()}
    overview = {
        "totalChaptersSent": 0,
        "totalMangaTracked": len(inp.wl_rows),
        "averageChaptersPerDay": 0.0,
        "avgCronDuration": 0.0,
        "queueLength": 0,
    }
    recent_chapters_list = []
    cron_status_data = None
    last_delivery = None
    try:
        recent_chapters_list, dh_count = build_recent_chapters(inp)
        overview["totalChaptersSent"] = dh_count
        overview["queueLength"] = build_queue_depth(inp)
        build_cron_averages(overview, inp.cron_stats)
        cron_status_data, last_delivery = build_cron_status(inp.cron_stats)
    except Exception:
        pass
    payload = {
        "success": True,
        "data": {
            "overview": overview,
            "sourceHealth": hm,
            "recentChapters": recent_chapters_list,
            "whitelistCount": len(inp.wl_rows),
            "queueLength": overview["queueLength"],
            "cronStatus": cron_status_data,
            "lastDelivery": last_delivery,
            "chaptersBySource24h": build_chapters_by_source(inp.sb_client),
        },
    }
    _SNAP_CACHE[0] = time.monotonic()
    _SNAP_CACHE[1] = payload
    return payload


def build_snapshot_sync() -> dict:
    """Run the (async) snapshot builder from a sync context (cron).

    Reuses the exact same computation as the API endpoint, so the
    persisted singleton row matches what /api/dashboard-snapshot returns.

    C3 FIX: Detect running event loop and run in thread to avoid
    asyncio.run() from running loop RuntimeError.
    """
    try:
        asyncio.get_running_loop()
        # We're inside a running loop — run snapshot in a separate thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, _build_snapshot()).result()
    except RuntimeError:
        # No running loop — safe to call asyncio.run()
        return asyncio.run(_build_snapshot())
