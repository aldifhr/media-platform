"""AnkerGames.net multiplayer PC-game scraper.

Uses the public JSON API at /api/v1/games (cursor pagination, sorted by
updated_at desc). Filters client-side for the ``Multiplayer`` genre because
the endpoint ignores query-string genre filters.

On first run only games updated within NEW_WINDOW_HOURS are considered new,
otherwise a cold-start would blast ~279 titles into the thread at once.
"""
from __future__ import annotations

import datetime as dt
import os
import urllib.parse

from modules.media.common.http import cf_get_json

BASE = "https://ankergames.net"
API = f"{BASE}/api/v1/games"
GENRE = "Multiplayer"
PER_PAGE = 100

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_ts(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _has_genre(game: dict, genre: str = GENRE) -> bool:
    return any(g.lower() == genre.lower() for g in game.get("genres", []))


def _build_api_url(per_page: int = PER_PAGE, cursor: str | None = None) -> str:
    qs = {"per_page": str(per_page)}
    if cursor:
        qs["cursor"] = cursor
    return f"{API}?{urllib.parse.urlencode(qs)}"


# ---------------------------------------------------------------------------
# public API (mirrors skidrow/ovagames/game3rb interface)
# ---------------------------------------------------------------------------


def fetch_listing(since: dt.datetime | None = None) -> list[dict]:
    """Return multiplayer games newer than ``since`` (or all, if ``since`` is None).

    Each item has at minimum: id, slug, title, subtitle, cover, poster,
    size_gb, release_date, version, genres, updated_at, url.
    """
    results: list[dict] = []
    cursor: str | None = None
    found_old = False

    while True:
        url = _build_api_url(cursor=cursor)
        data: dict = cf_get_json(url, timeout=30)
        meta = data.get("meta", {})
        games: list[dict] = data.get("data", [])
        if not games:
            break

        for g in games:
            if not _has_genre(g):
                continue

            updated = _parse_ts(g.get("updated_at"))
            if since and updated and updated < since:
                found_old = True
                continue

            results.append({
                "id": g["id"],
                "slug": g.get("slug", ""),
                "title": g.get("title", ""),
                "subtitle": g.get("subtitle", ""),
                "edition": g.get("edition") or "",
                "cover": g.get("cover") or g.get("poster") or "",
                "poster": g.get("poster") or "",
                "size_gb": g.get("size_gb"),
                "release_date": g.get("release_date") or "",
                "version": (g.get("version") or {}).get("label", ""),
                "genres": g.get("genres", []),
                "multiplayer": g.get("multiplayer"),
                "co_op": g.get("co_op"),
                "online_fix": g.get("online_fix"),
                "developer": g.get("developer") or "",
                "publisher": g.get("publisher") or "",
                "overview": (g.get("overview") or "")[:500],
                "updated_at": g.get("updated_at", ""),
                "store_url": g.get("store_url") or "",
                "steam_app_id": g.get("steam_app_id") or "",
                "files": g.get("files") or [],
                "url": f"{BASE}/game/{g.get('slug', str(g['id']))}",
            })

        if since and found_old:
            next_cursor = meta.get("next_cursor")
            if not next_cursor:
                break
            try:
                url2 = _build_api_url(cursor=next_cursor)
                data2: dict = cf_get_json(url2, timeout=30)
                games2: list[dict] = data2.get("data", [])
                any_new = False
                for g in games2:
                    if not _has_genre(g):
                        continue
                    up = _parse_ts(g.get("updated_at"))
                    if up is None or up >= since:
                        any_new = True
                        break
                if not any_new:
                    break
                cursor = next_cursor
            except Exception:
                break
        else:
            cursor = meta.get("next_cursor")
            if not cursor:
                break

    return results


def fetch_detail(game_id: int | str) -> dict | None:
    """Fetch a single game's detail from the API."""
    try:
        data: dict = cf_get_json(f"{API}/{game_id}", timeout=30)
        g = data.get("data", {})
        if not g:
            return None
        return {
            "version": (g.get("version") or {}).get("label", ""),
            "repack_features": (g.get("repack_features") or "")[:600],
            "system_requirements": (g.get("system_requirements") or "")[:600],
            "installation_guide": (g.get("installation_guide") or "")[:400],
            "screenshots": g.get("screenshots") or [],
            "trailer": (g.get("trailer") or {}).get("url", ""),
            "files": g.get("files") or [],
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# legacy shims (keep bot.py compatible with other sources)
# ---------------------------------------------------------------------------


def fetch_homepage() -> str:  # pragma: no cover
    raise NotImplementedError("ankergames uses API, not HTML homepage")


def parse_homepage(html: str) -> list[dict]:  # pragma: no cover
    raise NotImplementedError("ankergames uses API, not HTML homepage")


def parse_detail(html: str) -> dict:  # pragma: no cover
    raise NotImplementedError("ankergames uses API, not HTML detail")


def parse_posted(ts: str):  # pragma: no cover
    return _parse_ts(ts)


if __name__ == "__main__":
    now = _now_utc()
    items = fetch_listing()
    print(f"fetched {len(items)} multiplayer games (no since filter)")
    for it in items[:5]:
        print(f"  {it['id']} | {it['title'][:50]} | {it['updated_at']}")
