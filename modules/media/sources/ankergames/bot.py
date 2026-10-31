"""AnkerGames.net multiplayer PC-game notifier — main entrypoint.

  python ankergames/bot.py            # run once (cron)
  python ankergames/bot.py --loop     # run forever, sleep CHECK_INTERVAL_MINUTES
  python ankergames/bot.py --dry-run  # print what WOULD notify, send nothing
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

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import modules.media.sources.ankergames.notifier as notifier  # noqa: E402
import modules.media.sources.ankergames.scraper as scraper  # noqa: E402
from modules.media.common.state import State  # noqa: E402

CHECK_INTERVAL = int(os.getenv("ANKERGAMES_CHECK_INTERVAL_MINUTES", "30"))
NEW_WINDOW_HOURS = float(os.getenv("ANKERGAMES_NEW_WINDOW_HOURS", "24"))
SEEN = State("ankergames")


def run_once(dry_run: bool = False) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    try:
        items = scraper.fetch_listing()
    except Exception as e:
        print(f"[ankergames] fetch failed: {e}")
        return {"fetched": 0, "new": 0, "sent": 0, "error": str(e)}

    new_items = [it for it in items if not SEEN.is_seen(str(it["id"]))]

    # Apply time window: treat items with no parseable date as new
    cutoff = now - dt.timedelta(hours=NEW_WINDOW_HOURS)
    final = []
    for it in new_items:
        pub = scraper.parse_posted(it.get("updated_at", ""))
        if pub is None or pub >= cutoff:
            it["posted_date"] = pub.isoformat() if pub else None
            final.append(it)

    # Enrich with detail (already mostly present from listing, but grab extra)
    details: dict = {}
    for it in final:
        try:
            det = scraper.fetch_detail(it["id"])
            if det:
                details[str(it["id"])] = det
        except Exception as e:
            print(f"[ankergames] detail fetch failed for {it['slug']}: {e}")

    sent = notifier.send(final, dry_run=dry_run, details=details)
    if not dry_run:
        for it in final[:sent]:
            SEEN.mark_seen(str(it["id"]), value=it.get("updated_at", ""))
    print(f"[ankergames] fetched={len(items)} new={len(new_items)} sent={sent} (dry={dry_run})")
    return {"fetched": len(items), "new": len(new_items), "sent": sent}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.loop:
        print(f"[ankergames] loop mode, interval={CHECK_INTERVAL}m dry={args.dry_run}")
        while True:
            try:
                run_once(dry_run=args.dry_run)
            except Exception as e:
                print(f"[ankergames] loop error: {e}")
            time.sleep(CHECK_INTERVAL * 60)
    else:
        run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
