"""online-fix.me notifier — main entrypoint.

  run_once()            # one scrape+notify cycle (called by the scheduler)
"""
from __future__ import annotations

import datetime as dt
import os

from modules.media.common.env import env_int
from modules.media.common.state import State
from modules.media.common.translate_en import clean_dl_label, clean_ru_title, translate_ru_to_en
import modules.media.sources.onlinefix.notifier as notifier
import modules.media.sources.onlinefix.scraper as scraper

NEW_WINDOW_HOURS = env_int("ONLINEFIX_NEW_WINDOW_HOURS", 168)  # 7 days
_DESC_CHARS = 700
SEEN = State("onlinefix")


def _enrich(it: dict) -> None:
    """Fetch the detail page and attach an English title + description."""
    try:
        det = scraper.parse_detail(scraper.fetch_detail(it["url"]))
    except Exception as e:
        print(f"[online-fix] detail fetch failed for {it['slug']}: {e}")
        det = {}
    it["en_title"] = translate_ru_to_en(clean_ru_title(it["title"])) or it["title"]
    raw = det.get("description", "")
    it["en_desc"] = translate_ru_to_en(raw[:_DESC_CHARS]) if raw else ""
    it["downloads"] = [{"label": clean_dl_label(d["label"]), "url": d["url"]} for d in det.get("downloads", [])]


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

    for it in final:
        _enrich(it)
    sent = notifier.send(final, dry_run=dry_run)
    if not dry_run:
        for it in final[:sent]:
            SEEN.mark_seen(it["slug"], value=it.get("date", ""))
    print(f"[online-fix] fetched={len(items)} new={len(new_items)} would_send={len(final)} sent={sent} (dry={dry_run})")
    return {"fetched": len(items), "new": len(new_items), "would_send": len(final), "sent": sent}


if __name__ == "__main__":
    run_once(dry_run=os.getenv("DRY_RUN") == "1")
