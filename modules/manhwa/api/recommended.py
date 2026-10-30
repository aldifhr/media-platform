"""Recommended / Popular — GET /recommended (public).

Aggregates Shinigami top daily (KR) + manhua top (CN) into a single feed for
the home hero. No DB, no auth — pure upstream fan-out with 5-min cache.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger
from modules.manhwa.core.cover import scrub_cover

logger = get_logger("api:recommended")
router = APIRouter()

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 300.0  # 5 min


def _shinigami_series_url(manga_id: str) -> str:
    base = (settings.SHINIGAMI_PUBLIC_BASE or settings.SHINIGAMI_PUBLIC_URL or "https://11.shinigami.asia").rstrip("/")
    return f"{base}/series/{manga_id}"


def _fetch_shinigami(limit: int = 10) -> list[dict[str, Any]]:
    try:
        from curl_cffi import requests as cffi_req

        url = f"{settings.SHINIGAMI_API_BASE.rstrip('/')}/v1/manga/top"
        params = {"filter": "daily", "page": 1, "page_size": limit}
        r = cffi_req.get(url, params=params, impersonate="chrome", timeout=15)
        if r.status_code != 200:
            logger.warn("shinigami top failed", status=r.status_code)
            return []
        j = r.json()
        out: list[dict[str, Any]] = []
        for d in (j.get("data") or [])[:limit]:
            cover = d.get("cover_image_url") or d.get("cover_portrait_url") or ""
            if cover:
                cover = scrub_cover(cover) or cover
            slug = d.get("manga_id") or ""
            genres = []
            tax = d.get("taxonomy") or {}
            for g in (tax.get("Genre") or []):
                if g.get("name"):
                    genres.append(g["name"])
            out.append({
                "title": d.get("title") or "",
                "titleKey": slug,
                "slug": slug,
                "cover": cover,
                "seriesUrl": _shinigami_series_url(slug) if slug else "",
                "source": "shinigami",
                "rating": float(d.get("user_rate") or 0) or None,
                "views": int(d.get("view_count") or 0),
                "bookmarks": int(d.get("bookmark_count") or 0),
                "genres": genres,
                "description": (d.get("description") or "")[:300],
                "latestChapter": str(d.get("latest_chapter_number") or ""),
                "type": "manhwa",
                "origin": d.get("country_id") or "KR",
                "rank": d.get("rank"),
            })
        return out
    except Exception as e:
        logger.warn("shinigami fetch error", err=str(e)[:160])
        return []


def _fetch_shinigami_manhua(limit: int = 10) -> list[dict[str, Any]]:
    try:
        from curl_cffi import requests as cffi_req

        url = f"{settings.SHINIGAMI_API_BASE.rstrip('/')}/v1/manga/list"
        params = {
            "format": "manhua",
            "page": 1,
            "page_size": limit,
            "is_recommended": "true",
            "sort": "latest",
            "sort_order": "desc",
        }
        r = cffi_req.get(url, params=params, impersonate="chrome", timeout=15)
        if r.status_code != 200:
            logger.warn("shinigami manhua rec failed", status=r.status_code)
            return []
        j = r.json()
        out: list[dict[str, Any]] = []
        for d in (j.get("data") or [])[:limit]:
            cover = d.get("cover_image_url") or d.get("cover_portrait_url") or ""
            if cover:
                cover = scrub_cover(cover) or cover
            slug = d.get("manga_id") or ""
            genres = []
            tax = d.get("taxonomy") or {}
            for g in (tax.get("Genre") or []):
                if g.get("name"):
                    genres.append(g["name"])
            out.append({
                "title": d.get("title") or "",
                "titleKey": slug,
                "slug": slug,
                "cover": cover,
                "seriesUrl": _shinigami_series_url(slug) if slug else "",
                "source": "shinigami",
                "subSource": "manhua",
                "rating": float(d.get("user_rate") or 0) or None,
                "views": int(d.get("view_count") or 0),
                "bookmarks": int(d.get("bookmark_count") or 0),
                "genres": genres,
                "description": (d.get("description") or "")[:300],
                "latestChapter": str(d.get("latest_chapter_number") or ""),
                "type": "manhua",
                "origin": d.get("country_id") or "CN",
                "rank": d.get("rank"),
                "isRecommended": bool(d.get("is_recommended")),
            })
        return out
    except Exception as e:
        logger.warn("shinigami manhua fetch error", err=str(e)[:160])
        return []


async def _fetch_all(limit: int) -> list[dict[str, Any]]:
    sh, sh_manhua = await asyncio.gather(
        asyncio.to_thread(_fetch_shinigami, limit),
        asyncio.to_thread(_fetch_shinigami_manhua, limit),
    )
    # round-robin 2 sources: daily top (KR), manhua rec (CN)
    merged: list[dict[str, Any]] = []
    for i in range(max(len(sh), len(sh_manhua))):
        if i < len(sh):
            merged.append(sh[i])
        if i < len(sh_manhua):
            merged.append(sh_manhua[i])
    # dedup by titleKey
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for it in merged:
        k = it.get("titleKey") or it.get("slug") or ""
        if k and k in seen:
            continue
        seen.add(k)
        deduped.append(it)
    return deduped


@router.get("/recommended")
async def recommended(request: Request, limit: int = 20):
    """Get recommended/popular series from all sources."""
    now = time.time()
    _cache_key = f"recommended:limit={limit}"
    cached = _CACHE.get(_cache_key)
    if cached and (now - cached[0]) < _TTL:
        return JSONResponse(content=cached[1])

    try:
        items = await _fetch_all(limit)
    except Exception as e:
        logger.error("recommended fetch failed", err=str(e)[:160])
        items = []

    result = {
        "data": items,
        "cached_at": now,
        "source": "recommended",
    }
    _CACHE[_cache_key] = (now, result)
    return JSONResponse(content=result)
