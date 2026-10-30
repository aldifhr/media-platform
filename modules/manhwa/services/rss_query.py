"""RSS query helpers — filtering, result mapping, and grouping."""
from __future__ import annotations

import html
import re

from modules.manhwa.core.text import normalize_title_key
from modules.manhwa.core.origin import normalize_origin
from modules.manhwa.core.cover import scrub_cover
from modules.manhwa.logger import get_logger

logger = get_logger("services:rss_query")

def normalize_type(raw) -> str | None:
    if not raw:
        return None
    t = str(raw).strip().lower()
    if t in ("manhwa", "manhua", "manga"):
        return t
    if t.startswith("manh"):
        return "manhwa" if t.endswith("wa") else "manhua"
    return t or None

_SLUG_RE = re.compile(r"[^a-z0-9]+")

def _slug_key(tk: str) -> str:
    t = normalize_title_key(tk).lower()
    return _SLUG_RE.sub("-", t).strip("-")


def _is_sent(it: dict, tk: str, src: str, dh_sent: set[tuple[str, float]] | None) -> bool:
    if not dh_sent:
        return False
    _cn_raw = it.get("chapter_num")
    try:
        cn = float(_cn_raw) if _cn_raw is not None else None
    except (ValueError, TypeError):
        cn = None
    if cn is None:
        cn = chapter_number(str(it.get("chapter") or ""))
    if cn is None:
        return False
    nk = normalize_title_key(tk)
    return (tk, cn) in dh_sent or (nk, cn) in dh_sent

def chapter_label(ch: str) -> str:
    from modules.manhwa.services.shared import chapter_label as _chapter_label
    return _chapter_label(ch)

def chapter_number(ch: str) -> float | None:
    from modules.manhwa.services.fcfs import parse_chapter_number as _parse_chapter_number
    return _parse_chapter_number(ch)

def group_key(value: str) -> str:
    key = normalize_title_key(value or "")
    for art in ("the ", "a ", "an "):
        if key.startswith(art):
            key = key[len(art):]
            break
    return key

def build_filter(
    source_f: str = "",
    origin_f: str = "",
    exclude: str = "",
    q: str = "",
    exclude_origin: str = "",
    type_f: str = "",
) -> callable:
    def _passes(it: dict) -> bool:
        src = it.get("source", "")
        o = (it.get("origin") or "").upper()
        if source_f and src != source_f:
            return False
        if origin_f and o != origin_f.upper():
            return False
        if exclude and o in [e.strip().upper() for e in exclude.split(",") if e.strip()]:
            return False
        if q and q.lower() not in (it.get("title") or "").lower():
            return False
        if exclude_origin and o in [e.strip().upper() for e in exclude_origin.split(",") if e.strip()]:
            return False
        if type_f and (it.get("type") or "").lower() != type_f.lower():
            return False
        return True
    return _passes

def map_result(
    it: dict,
    wl_map: dict[tuple[str, str], dict],
    sm_map: dict[tuple[str, str], dict] | None = None,
    dh_sent: set[tuple[str, float]] | None = None,
    wl_title_set: set[str] | None = None,
) -> dict:
    """Map a recent_chapters row to RSS response. Uses it + wl + sm only.

    """
    if sm_map is None:
        sm_map = {}
    tk = it.get("title_key", "")
    src = it.get("source", "")
    nk = normalize_title_key(tk)
    wl = wl_map.get((tk, src)) or wl_map.get((nk, src), {}) or {}
    sm = sm_map.get((tk, src)) or sm_map.get((nk, src), {}) or {}

    # Source-strict badge: "Verified" means THIS (title, source) pair is
    # whitelisted. A title tracked on shinigami must not show Verified on its
    # other-source row — that badge is what the user reads to decide whether
    # to add the series, and claiming it for a source they never subscribed to
    # is simply wrong (and hid the Add button, so the row could never be added).
    #
    # Dispatch matches the same way: filter_whitelisted() in cron/collect.py is
    # per (title_key, source), so a notification can only arrive for a source
    # the user actually subscribed to. Only FCFS (fcfs_key, title+chapter) stays
    # cross-source — that answers "is this the same chapter", a different
    # delivery. They are different questions.
    is_wl = (tk, src) in wl_map or (nk, src) in wl_map
    _title_norm = normalize_title_key(it.get("title") or "")

    series_url = it.get("series_url") or wl.get("series_url") or sm.get("series_url") or ""
    cover = scrub_cover(it.get("cover") or wl.get("cover") or sm.get("cover") or "")

    chapter_url = it.get("chapter_url") or ""
    _raw_title = it.get("title", "") or ""
    _title = html.unescape(_raw_title) if _raw_title else ""
    _genres_raw = sm.get("genres") or it.get("genres") or wl.get("genres") or []
    _genres_seen: set[str] = set()
    _genres: list[str] = []
    if isinstance(_genres_raw, list):
        for g in _genres_raw:
            if not isinstance(g, str):
                continue
            _gl = g.strip().lower()
            if _gl and _gl not in _genres_seen:
                _genres_seen.add(_gl)
                _genres.append(g.strip())

    from modules.manhwa.core.rating_utils import normalize_rating as _nr  # 1-10 contract

    _rating = _nr(sm.get("rating")) if sm.get("rating") not in (None, "") else (_nr(it.get("rating")) if it.get("rating") not in (None, "") else _nr(wl.get("rating")))
    _type = normalize_type(sm.get("type") or it.get("type") or wl.get("type") or None)
    # `origin` is NULL on every Shinigami row, so fall back to the format
    # (manhwa/manga/manhua), which normalize_origin also understands. Without
    # this the FE gets country=null and has to guess from `format` itself.
    _raw_origin = it.get("origin") or wl.get("origin") or sm.get("origin") or _type or ""
    origin = normalize_origin(_raw_origin)

    _raw_chapter_num = it.get("chapter_num")
    try:
        _chapter_num_f = float(_raw_chapter_num) if _raw_chapter_num is not None else 0.0
    except (ValueError, TypeError):
        _chapter_num_f = 0.0
    slug = _slug_key(tk)
    return {
        "id": slug,
        "title": _title,
        "titleKey": slug,
        "source": src,
        "cover": cover,
        "seriesUrl": series_url,
        "country": origin,
        "format": _type,
        "genres": _genres,
        "rating": _rating,
        "description": sm.get("description") or it.get("description") or wl.get("description") or "",
        "isWhitelisted": is_wl,
        "chapterLabel": chapter_label(str(it.get("chapter") or "")),
        "chapterNumber": chapter_number(str(it.get("chapter") or "")),
        "chapterUrl": chapter_url,
        "isSent": _is_sent(it, tk, src, dh_sent),
        "createdAt": it.get("updated_time") or it.get("created_at"),
    }

def group_results(results: list[dict]) -> list[dict]:
    """Group RSS results by (canonicalTitleKey, source) — per-source split.
    Groups are sorted by most recent activity (latest updated_time).
    """
    groups: dict[str, dict] = {}
    for r in results:
        gk_raw = r.get("canonicalTitleKey") or r.get("titleKey") or r.get("title") or ""
        gk = f"{group_key(gk_raw)}|{r.get('source') or ''}"
        if gk not in groups:
            groups[gk] = {
                "title": r["title"],
                "titleKey": r["titleKey"],
                "source": r["source"],
                "sources": list(r.get("sources") or []),
                "cover": r.get("cover"),
                "seriesUrl": r.get("seriesUrl"),
                "country": r.get("country") or r.get("origin"),
                "format": r.get("format"),
                "rating": r.get("rating"),
                "genres": r.get("genres"),
                "description": r.get("description") or "",
                "isWhitelisted": r.get("isWhitelisted"),
                "latestUpdated": r.get("updated_time") or r.get("createdAt") or "",
                "chapters": [],
            }
        else:
            cur = groups[gk]
            cur["isWhitelisted"] = cur["isWhitelisted"] or r["isWhitelisted"]
            for s in r.get("sources") or []:
                if s not in cur["sources"]:
                    cur["sources"].append(s)
            if not cur.get("cover") and r.get("cover"):
                cur["cover"] = r["cover"]
            if not cur.get("seriesUrl") and r.get("seriesUrl"):
                cur["seriesUrl"] = r["seriesUrl"]
            if cur.get("rating") is None and r.get("rating") is not None:
                cur["rating"] = r["rating"]
            if not cur.get("genres") and r.get("genres"):
                cur["genres"] = r["genres"]
            # Track most recent updated_time
            new_updated = r.get("updated_time") or r.get("createdAt") or ""
            if new_updated > (cur.get("latestUpdated") or ""):
                cur["latestUpdated"] = new_updated
            try:
                if float(r.get("lastCheckedChapter") or 0) > float(cur.get("lastCheckedChapter") or 0):
                    cur["lastCheckedChapter"] = r["lastCheckedChapter"]
            except (ValueError, TypeError):
                pass
            try:
                if r.get("latestSentChapter") is not None and (cur.get("latestSentChapter") is None or float(r["latestSentChapter"]) > float(cur["latestSentChapter"] or 0)):
                    cur["latestSentChapter"] = r["latestSentChapter"]
            except (ValueError, TypeError):
                pass
            try:
                if float(r.get("latestChapter") or 0) > float(cur.get("latestChapter") or 0):
                    cur["latestChapter"] = r["latestChapter"]
            except (ValueError, TypeError):
                pass
        groups[gk]["chapters"].append({
            "chapterLabel": r["chapterLabel"],
            "chapterNumber": r["chapterNumber"],
            "url": r["chapterUrl"],
            "source": r["source"],
            "isSent": r["isSent"],
        })
    for g in groups.values():
        g["chapters"].sort(key=lambda c: c.get("chapterNumber") or 0, reverse=True)
    return sorted(groups.values(), key=lambda g: g.get("latestUpdated") or "", reverse=True)
