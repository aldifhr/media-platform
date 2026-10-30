from __future__ import annotations

import time as _qtime
import re
from collections import OrderedDict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.manhwa.logger import get_logger
from modules.manhwa.core.text import slugify_title_key, normalize_title_key
from modules.manhwa.utils.request_auth import safe_error
from modules.manhwa.utils.responses import err

logger = get_logger("api:catalog:chapters")
router = APIRouter()

_CAT_CH_CACHE: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_CAT_CH_MAX = 256
_CAT_CH_TTL = 300


@router.get("/catalog/{title_key}/chapters")
async def catalog_chapters(title_key: str, request: Request):
    # public read — chapter list page
    if not re.match(r"^[a-zA-Z0-9\-_ ]+$", title_key):
        return err("invalid title_key", status_code=400)
    from modules.manhwa.db import get_supabase
    _source_q = (request.query_params.get("source") or "").strip().lower()
    norm_tk = normalize_title_key(title_key)
    slug = slugify_title_key(title_key)
    _now = _qtime.monotonic()
    _cache_key = f"cat_ch:{norm_tk}:{_source_q}" if _source_q else f"cat_ch:{norm_tk}"
    _cached = _CAT_CH_CACHE.get(_cache_key)
    if _cached is not None and (_now - _cached[0]) < _CAT_CH_TTL:
        return JSONResponse(content=_cached[1])
    sb = get_supabase()
    rows: list[dict] = []
    try:
        # DB first — recent_chapters matching this title_key
        rc_res = sb.table("recent_chapters").select(
            "source, chapter, chapter_url, cover, series_url, origin, updated_time, release_date"
        ).in_("title_key", [norm_tk, slug, title_key]).limit(200).execute()
        seen: set[str] = set()
        for rc in (rc_res.data or []):
            ch = str(rc.get("chapter") or "")
            src = rc.get("source") or ""
            url = rc.get("chapter_url") or ""
            if ch and url:
                key = f"{src}:{ch}"
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "title_key": norm_tk,
                    "title": "",
                    "source": src,
                    "chapter": ch,
                    "chapter_url": url,
                    "cover": rc.get("cover") or "",
                    "series_url": rc.get("series_url") or "",
                    "origin": rc.get("origin") or "",
                    "updated_time": rc.get("updated_time") or "",
                })
        if not rows:
            # upstream fallback for Popular (not yet in DB) — shinigami UUID
            try:
                import re as _re3
                is_uuid = bool(_re3.match(r"^[0-9a-fA-F-]{36}$", title_key))
                if is_uuid:
                    from modules.manhwa.sources.shinigami import get_shinigami_chapters
                    from modules.manhwa.config import settings as _s
                    chs = get_shinigami_chapters(title_key, per_page=100)
                    for ch in chs[:100]:
                        num = str(ch.get("chapter_number") or ch.get("number") or "")
                        cid = ch.get("chapter_id") or ch.get("id") or ""
                        url = f"{_s.SHINIGAMI_PUBLIC_BASE.rstrip('/')}/chapter/{cid}" if cid else ""
                        rows.append({"title_key": norm_tk, "title": "", "source": "shinigami", "chapter": num, "chapter_url": url, "cover": "", "series_url": f"{_s.SHINIGAMI_PUBLIC_BASE.rstrip('/')}/series/{title_key}", "origin": "KR", "updated_time": ch.get("created_at") or ch.get("updated_at") or ""})
            except Exception as e:
                logger.warn("catalog_chapters: shinigami fetch failed", title_key=title_key[:40], err=str(e)[:120])
        payload = {"success": True, "data": {"results": rows, "total": len(rows), "titleKey": norm_tk}}
        _CAT_CH_CACHE[norm_tk] = (_qtime.monotonic(), payload)
        _CAT_CH_CACHE.move_to_end(norm_tk)
        while len(_CAT_CH_CACHE) > _CAT_CH_MAX:
            _CAT_CH_CACHE.popitem(last=False)
        return JSONResponse(content=payload)
    except Exception as e:
        return JSONResponse(content=safe_error(e), status_code=500)
