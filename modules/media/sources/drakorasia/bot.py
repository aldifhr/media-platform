"""Drakorasia drama notifier — main entrypoint.

  python drakorasia/bot.py            # run once
  python drakorasia/bot.py --loop     # run forever, sleep CHECK_INTERVAL_MINUTES
  python drakorasia/bot.py --dry-run  # print what WOULD notify, send nothing

Scrapes both category listings (drama-korea, drama-china); each category is
delivered to its own Discord thread.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time


def _load_env() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(root, ".env")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import modules.media.sources.drakorasia.notifier as notifier  # noqa: E402
import modules.media.sources.drakorasia.scraper as scraper  # noqa: E402
from modules.media.common.state import State  # noqa: E402

CHECK_INTERVAL = int(os.getenv("DRAKORASIA_CHECK_INTERVAL_MINUTES", "30"))
# How far back (hours) a published date is still considered "new" on first run.
NEW_WINDOW_HOURS = float(os.getenv("DRAKORASIA_NEW_WINDOW_HOURS", "48"))
SEEN = State("drakorasia")


def run_once(dry_run: bool = False) -> dict:
    now = dt.datetime.now(dt.UTC)
    items: list[dict] = []
    for cat in scraper.CATEGORIES:
        try:
            items.extend(scraper.parse_category(scraper.fetch_category(cat), cat)[:10])
        except Exception as e:
            print(f"[drakorasia] {cat} fetch failed: {e}")

    new_items = [it for it in items if not SEEN.is_seen(scraper.ep_key(it))]

    # Detail enrichment (synopsis + cover) only for genuinely-new items.
    details: dict = {}
    for it in new_items:
        try:
            details[it["slug"]] = scraper.parse_detail(scraper.fetch_detail(it["url"]))
        except Exception as e:
            print(f"[drakorasia] detail fetch failed for {it['slug']}: {e}")

    # Time window on the feed's datePublished; an unparseable date is new.
    cutoff = now - dt.timedelta(hours=NEW_WINDOW_HOURS)
    final = [
        it
        for it in new_items
        if (pub := scraper.parse_published(it.get("published", ""))) is None or pub >= cutoff
    ]

    sent = notifier.send(final, dry_run=dry_run, details=details)
    if not dry_run:
        # Only mark seen what a thread actually exists for — an unconfigured
        # category must stay unseen so it notifies once the thread is set.
        for it in final:
            if notifier.thread_for(it.get("category") or "korea"):
                SEEN.mark_seen(scraper.ep_key(it), value=it.get("published", ""))
    print(f"[drakorasia] fetched={len(items)} new={len(new_items)} sent={sent} (dry={dry_run})")
    return {"fetched": len(items), "new": len(new_items), "sent": sent}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.loop:
        print(f"[drakorasia] loop mode, interval={CHECK_INTERVAL}m dry={args.dry_run}")
        while True:
            try:
                run_once(dry_run=args.dry_run)
            except Exception as e:
                print(f"[drakorasia] loop error: {e}")
            time.sleep(CHECK_INTERVAL * 60)
    else:
        run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
