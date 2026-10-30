"""online-fix.me notifier — main entrypoint.

  run_once()            # one scrape+notify cycle (called by the scheduler)
"""
from __future__ import annotations

import datetime as dt
import os

from modules.media.common.env import env_int
from modules.media.common.state import State
import modules.media.sources.onlinefix.notifier as notifier
import modules.media.sources.onlinefix.scraper as scraper

NEW_WINDOW_HOURS = env_int("ONLINEFIX_NEW_WINDOW_HOURS", 168)  # 7 days
SEEN = State("onlinefix")


def run_once(dry_run: bool = False) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    try:
        html = scraper.fetch_homepage()
    except Exception as e:
        print(f"[online-fix] fetch failed: {e}")
        return {"fetched": 0, "new": 0, "sent": 0, "error": str(e)}

    items = scraper.parse_homepage(html)
    new_items = [it for it in items if not SEEN.is_seen(it["slug"])]

    # On first run only notify items inside the window; undated items pass.
    cutoff = now - dt.timedelta(hours=NEW_WINDOW_HOURS)
    final = []
    for it in new_items:
        pub = scraper.parse_posted(it.get("date", ""))
        if pub is None or pub >= cutoff:
            final.append(it)

    sent = notifier.send(final, dry_run=dry_run)
    if not dry_run:
        for it in final[:sent]:
            SEEN.mark_seen(it["slug"], value=it.get("date", ""))
    print(f"[online-fix] fetched={len(items)} new={len(new_items)} would_send={len(final)} sent={sent} (dry={dry_run})")
    return {"fetched": len(items), "new": len(new_items), "would_send": len(final), "sent": sent}


if __name__ == "__main__":
    run_once(dry_run=os.getenv("DRY_RUN") == "1")
