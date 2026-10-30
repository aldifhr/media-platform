"""Catalog list — GET /catalog."""

from fastapi import APIRouter, Request
from modules.manhwa.services.rss_query import chapter_number
from modules.manhwa.core.text import escape_like
from modules.manhwa.logger import get_logger
from modules.manhwa.services.whitelist_service import load_whitelist as _load_whitelist
from modules.manhwa.utils.request_auth import int_safe, require_monitor_auth
from modules.manhwa.utils.responses import err, ok, unauthorized
from modules.manhwa.core.cover import batch_cover_ref

logger = get_logger("api:catalog:list")
router = APIRouter()

_MAX_CATALOG_EXPORT = 1000


@router.get("/catalog")
async def catalog_list(request: Request):
    if not require_monitor_auth(request):
        return unauthorized()
    page = int_safe(request.query_params.get("page", "1"), 1)
    page_size = int_safe(request.query_params.get("page_size", "20"), 20, max_val=_MAX_CATALOG_EXPORT)
    search = (request.query_params.get("search", "") or request.query_params.get("q", ""))[:100]
    if len(request.query_params.get("search", "") or request.query_params.get("q", "") or "") > 100:
        return err("search too long (max 100)", status_code=400)
    source = request.query_params.get("source", "")
    title = (request.query_params.get("title", "") or "")[:200]
    if len(request.query_params.get("title", "") or "") > 200:
        return err("title too long (max 200)", status_code=400)
    type_f = request.query_params.get("type", "").strip().lower()
    origin_f = request.query_params.get("origin", "").strip().upper()
    all_param = request.query_params.get("all") == "true"
    import asyncio as _asyncio2
    from modules.manhwa.db import get_supabase
    try:
        sb = get_supabase()
        q = sb.table("whitelist").select("*", count="exact")
        if source:
            q = q.eq("source", source)
        if type_f:
            q = q.eq("type", type_f)
        if origin_f:
            q = q.eq("origin", origin_f)
        if search:
            q = q.ilike("title", f"%{escape_like(search)}%")
        if title:
            q = q.ilike("title", f"%{escape_like(title)}%")
        if all_param:
            res = await _asyncio2.to_thread(lambda: q.order("created_at", desc=True).limit(_MAX_CATALOG_EXPORT).execute())
            rows = res.data or []
            total = len(rows)
            paged = rows
            total_pages = 1
            page = 1
            page_size = total or 1
        else:
            cnt_res = await _asyncio2.to_thread(lambda: q.limit(1).execute())
            total = cnt_res.count or 0
            start = (page - 1) * page_size
            q2 = sb.table("whitelist").select("*")
            if source:
                q2 = q2.eq("source", source)
            if type_f:
                q2 = q2.eq("type", type_f)
            if origin_f:
                q2 = q2.eq("origin", origin_f)
            if search:
                q2 = q2.ilike("title", f"%{escape_like(search)}%")
            if title:
                q2 = q2.ilike("title", f"%{escape_like(title)}%")
            paged_res = await _asyncio2.to_thread(lambda: q2.order("created_at", desc=True).limit(page_size).offset(start).execute())
            paged = paged_res.data or []
            rows = paged
            total_pages = (total + page_size - 1) // page_size if page_size else 1
    except Exception:
        rows = _load_whitelist()
        if source:
            rows = [r for r in rows if r.get("source") == source]
        if search:
            sq = search.lower()
            rows = [r for r in rows if sq in (r.get("title", "") or "").lower()]
        if title:
            tq = title.lower()
            rows = [r for r in rows if tq in (r.get("title", "") or "").lower()]
        total = len(rows)
        if all_param:
            page_size = total or 1
            page = 1
            start = 0
            end = total
        else:
            start = (page - 1) * page_size
            end = start + page_size
        paged = rows[start:end]
        total_pages = (total + page_size - 1) // page_size if page_size else 1
    meta_rows = {}
    latest_rows: dict[str, dict] = {}
    if paged:
        tks = [r.get("title_key", "") for r in paged if r.get("title_key")]
        if tks:
            from modules.manhwa.services.metadata_service import batch_get_manga_metadata as _bgmm
            mrows = _bgmm(tks)
            for tk, m in zip(tks, mrows):
                if m:
                    meta_rows[tk] = m
            try:
                lr = get_supabase().table("recent_chapters").select("*").in_("title_key", tks).execute()
                for row in (lr.data or []):
                    tk = row.get("title_key", "")
                    if tk not in latest_rows:
                        latest_rows[tk] = row
            except Exception as e:
                logger.warn("catalog_list: recent_chapters latest lookup failed", err=str(e)[:120])
    covers = batch_cover_ref(tks) if paged else {}
    results = []
    for r in paged:
        tk = r.get("title_key", "")
        src = r.get("source", "") or ""
        cached = meta_rows.get(tk) or {}
        lc = latest_rows.get(tk) or {}
        status = r.get("status") or cached.get("status") or "unknown"
        rating = r.get("rating") if r.get("rating") is not None else cached.get("rating")
        origin = r.get("origin") or cached.get("origin") or ""
        gen = r.get("genres") or cached.get("genres") or []
        desc = r.get("description") or cached.get("description") or ""
        latest_ch = None
        if lc:
            ch_str = str(lc.get("chapter") or "0")
            ch_num = chapter_number(ch_str)
            latest_ch = {"number": ch_num if ch_num is not None else 0, "url": lc.get("chapter_url", ""), "sentAt": lc.get("updated_time", ""), "source": lc.get("source", "")}
        results.append({"titleKey": tk, "title": r.get("title", "") or cached.get("title", ""), "cover": covers.get(tk, ""), "status": status, "source": src, "rating": rating, "type": r.get("type") or cached.get("type") or None, "sources": [src] if src else [], "metadata": {"status": status, "rating": rating, "genres": gen, "description": desc, "origin": origin or src}, "latestChapter": latest_ch})
    return ok({"results": results, "total": total, "page": page, "pageSize": page_size, "totalPages": total_pages})
