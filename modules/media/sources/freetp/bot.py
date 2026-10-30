"""freetp.org notifier — main entrypoint.

  run_once()            # one scrape+notify cycle (called by the scheduler)
"""
from __future__ import annotations

import os

from modules.media.common.env import env_int
from modules.media.common.state import State
from modules.media.common.translate_en import clean_dl_label, clean_ru_title, translate_ru_to_en
import modules.media.sources.freetp.notifier as notifier
import modules.media.sources.freetp.scraper as scraper

# Cards carry no date, so cap the per-cycle take to the newest N (limits the
# one-time first-run flood; steady state only ever has a few new).
NEWEST_N = env_int("FREETP_NEWEST_N", 8)
_DESC_CHARS = 700
SEEN = State("freetp")


def _enrich(it: dict) -> None:
    """Fetch the detail page and attach an English title + description."""
    try:
        det = scraper.parse_detail(scraper.fetch_detail(it["url"]))
    except Exception as e:
        print(f"[freetp] detail fetch failed for {it['slug']}: {e}")
        det = {}
    it["en_title"] = translate_ru_to_en(clean_ru_title(it["title"])) or it["title"]
    raw = det.get("description", "")
    it["en_desc"] = translate_ru_to_en(raw[:_DESC_CHARS]) if raw else ""
    it["downloads"] = [{"label": clean_dl_label(d["label"]), "url": d["url"]} for d in det.get("downloads", [])]


def run_once(dry_run: bool = False) -> dict:
    try:
        html = scraper.fetch_homepage()
    except Exception as e:
        print(f"[freetp] fetch failed: {e}")
        return {"fetched": 0, "new": 0, "sent": 0, "error": str(e)}

    items = scraper.parse_homepage(html)[:NEWEST_N]
    new_items = [it for it in items if not SEEN.is_seen(it["slug"])]
    for it in new_items:
        _enrich(it)
    sent = notifier.send(new_items, dry_run=dry_run)
    if not dry_run:
        for it in new_items[:sent]:
            SEEN.mark_seen(it["slug"])
    print(f"[freetp] fetched={len(items)} new={len(new_items)} sent={sent} (dry={dry_run})")
    return {"fetched": len(items), "new": len(new_items), "sent": sent}


if __name__ == "__main__":
    run_once(dry_run=os.getenv("DRY_RUN") == "1")
