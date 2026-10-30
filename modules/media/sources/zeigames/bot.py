"""ZeiGames notifier — main entrypoint."""
from __future__ import annotations

import argparse
import os
import sys
import time
import datetime as dt


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

import modules.media.sources.zeigames.scraper as scraper  # noqa: E402
import modules.media.sources.zeigames.notifier as notifier  # noqa: E402
from modules.media.common.state import State  # noqa: E402

CHECK_INTERVAL = int(os.getenv("ZEIGAMES_CHECK_INTERVAL_MINUTES", "30"))
NEW_WINDOW_HOURS = float(os.getenv("ZEIGAMES_NEW_WINDOW_HOURS", "24"))
SEEN = State("zeigames")


def run_once(dry_run: bool = False) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    try:
        html = scraper.fetch_tag_page()
    except Exception as e:
        print(f"[zeigames] fetch failed: {e}")
        return {"fetched": 0, "new": 0, "sent": 0, "error": str(e)}

    items = scraper.parse_tag_page(html)[:5]
    new_items = [it for it in items if not SEEN.is_seen(it["slug"])]

    details: dict = {}
    for it in new_items:
        try:
            dh = scraper.fetch_detail(it["url"])
            details[it["slug"]] = scraper.parse_detail(dh)
        except Exception as e:
            print(f"[zeigames] detail fetch failed for {it['slug']}: {e}")

    final = []
    for it in new_items:
        det = details.get(it["slug"]) or {}
        pub = scraper.parse_posted(det.get("posted", ""))
        it["posted_date"] = pub.isoformat() if pub else None
        final.append(it)

    sent = notifier.send(final, dry_run=dry_run, details=details, source="zeigames")
    if not dry_run:
        for it in final[:sent]:
            det = details.get(it["slug"]) or {}
            SEEN.mark_seen(it["slug"], value=det.get("posted", ""))
    print(f"[zeigames] fetched={len(items)} new={len(new_items)} sent={sent} (dry={dry_run})")
    return {"fetched": len(items), "new": len(new_items), "sent": sent}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.loop:
        print(f"[zeigames] loop mode, interval={CHECK_INTERVAL}m dry={args.dry_run}")
        while True:
            try:
                run_once(dry_run=args.dry_run)
            except Exception as e:
                print(f"[zeigames] loop error: {e}")
            time.sleep(CHECK_INTERVAL * 60)
    else:
        run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
