"""Shinigami per-series collector."""
from modules.manhwa.config import settings
from modules.manhwa.cron.collectors.common import apply_series_meta_fallback, build_item
from modules.manhwa.logger import get_logger
from modules.manhwa.core.rating_utils import normalize_rating
from modules.manhwa.storage.series_meta import series_meta
from modules.manhwa.services.fcfs import parse_chapter_number as _parse_chapter_num

logger = get_logger("cron:collect:shinigami")


def _derive_type(m: dict, origin: str, meta_item: dict) -> str:
    """Derive content type (manhwa/manga/manhua) from multiple sources."""
    _tax = m.get("taxonomy") or {}
    
    # 1. Primary: taxonomy.Format from API (most reliable)
    if isinstance(_tax, dict):
        _format = _tax.get("Format")
        if isinstance(_format, list) and _format and _format[0].get("name"):
            return str(_format[0]["name"]).lower()
    
    # 2. Fallback: country → type
    _country = str(m.get("country_id") or "").upper()
    if _country == "KR":
        return "manhwa"
    elif _country == "CN":
        return "manhua"
    elif _country == "JP":
        return "manga"
    
    # 3. Fallback: origin string override
    if origin == "CN":
        return "manhua"
    elif origin == "KR":
        return "manhwa"
    
    # 4. Last resort: series_meta
    if isinstance(meta_item, dict) and meta_item.get("type"):
        return (meta_item.get("type") or "").lower()
    
    return ""


def _collect_shinigami_source(latest_sent: dict, disabled: set, fetch_meta: bool = True) -> list[dict]:
    from datetime import datetime, timezone, timedelta

    from modules.manhwa.sources import shinigami as _shinigami_scraper
    from modules.manhwa.core.text import slugify_title_key as _ntk

    # 3 most recent chapters regardless of age (e.g. ch 25 today + ch 24 from 7 days
    # ago). Without cutoff, old chapters flood recent_chapters + Discord.
    try:
        _lookback = int(getattr(settings, "RSS_LOOKBACK_HOURS", 24))
    except Exception:
        _lookback = 24
    _cutoff = datetime.now(timezone.utc) - timedelta(hours=_lookback)

    items: list[dict] = []
    _series: list[dict] = []
    try:
        _series.extend(_shinigami_scraper.get_shinigami_latest_updates() or [])
    except Exception as _pe:
        logger.warn("shinigami latest fetch failed", err=str(_pe)[:120])
        return items
    
    # ── Pass 1: decide which series need a full chapter list ──
    #
    # /manga/list embeds only 3 chapters, so a series with >3 releases in the
    # lookback window is truncated. Fetching /chapter/{id}/list for those is
    # required for correctness — but "embedded exactly 3" is the NORMAL shape of
    # that response, so the bulk path fires for nearly every series. Doing it
    # inline cost one serial HTTP call per series: measured 10.3s for 57 series
    # in the live pipeline log (the 260ms collect right before it was the same
    # endpoint with the same data).
    #
    # So: collect the ids first, fetch them concurrently, then build items.
    _bulk_ids: list[str] = []
    _series_prepared: list[tuple[dict, str, list[dict]]] = []
    for m in _series:
        title = m.get("title") or m.get("manga_name") or ""
        if not title:
            continue
        chaps = m.get("chapters", []) or []
        if len(chaps) == 3:
            _oldest_ts = chaps[-1].get("created_at") or chaps[-1].get("release_date") or ""
            try:
                _oldest_dt = datetime.fromisoformat(str(_oldest_ts).replace("Z", "+00:00"))
                if _oldest_dt.tzinfo is None:
                    _oldest_dt = _oldest_dt.replace(tzinfo=timezone.utc)
                _mid0 = m.get("manga_id") or ""
                if _oldest_dt >= _cutoff and _mid0:
                    _bulk_ids.append(_mid0)
            except (ValueError, TypeError):
                pass
        _series_prepared.append((m, _ntk(title), chaps))

    _bulk_chaps: dict[str, list[dict]] = {}
    if _bulk_ids:
        import concurrent.futures

        def _fetch_one(mid: str) -> tuple[str, list[dict]]:
            try:
                _full = _shinigami_scraper.get_shinigami_chapters(mid, per_page=100)
            except Exception as _fe:
                logger.debug("shinigami bulk fetch failed, using embedded", manga_id=mid, err=str(_fe)[:120])
                return mid, []
            if not _full:
                return mid, []
            return mid, [
                {
                    "chapter_id": c.get("chapter_id") or c.get("id") or "",
                    "chapter_number": c.get("chapter_number") or c.get("number") or "",
                    "created_at": c.get("release_date") or c.get("created_at") or "",
                    "release_date": c.get("release_date") or c.get("created_at") or "",
                }
                for c in _full
            ]

        # 8 workers: enough to overlap the ~180ms calls without tripping the
        # site's rate limit. ponytail: if 429s appear, lower this to 4.
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as _ex:
            for _mid, _ch in _ex.map(_fetch_one, _bulk_ids):
                if _ch:
                    _bulk_chaps[_mid] = _ch

    # ── Pass 2: build items ──
    for m, tk, chaps in _series_prepared:
        title = m.get("title") or m.get("manga_name") or ""
        origin = (m.get("country_id") or "").upper()
        cover = m.get("cover_image_url") or m.get("cover_portrait_url") or ""
        rating = normalize_rating(m.get("user_rate")) if m.get("user_rate") else 0.0
        description = (m.get("description") or "").strip()

        _meta_item: dict = {}
        if fetch_meta:
            _meta_item = series_meta.get("shinigami", tk)
        rating, description, _genres_meta, _type_meta = apply_series_meta_fallback(
            rating=rating,
            description=description,
            genres=[],
            content_type="",
            meta_item=_meta_item,
        )

        _tax = m.get("taxonomy") or {}
        if isinstance(_tax, dict):
            genres = [g.get("name") for g in (_tax.get("Genre") or []) if g.get("name")]
        else:
            genres = []

        # Derive type using centralized function
        _type2 = _derive_type(m, origin, _meta_item)

        series_url = f"{settings.SHINIGAMI_PUBLIC_BASE}/series/{m.get('manga_id', '')}"

        # Swap in the full chapter list when the bulk fetch produced one;
        # otherwise the embedded 3 are all we have and are used as-is.
        _full_for_m = _bulk_chaps.get(m.get("manga_id") or "")
        if _full_for_m:
            chaps = _full_for_m
        
        for ch in chaps:
            ch_id = ch.get("chapter_id") or ""
            if not ch_id:
                continue
            ch_str = str(ch.get("chapter_number") or "")
            chapter_url = f"{settings.SHINIGAMI_PUBLIC_BASE}/chapter/{ch_id}"
            _chn = _parse_chapter_num(ch_str)
            _ceil = latest_sent.get((tk, "shinigami"), 0)
            if _chn is not None and _ceil and _chn <= _ceil:
                continue
            
            # 24h cutoff — skip chapters older than RSS_LOOKBACK_HOURS
            _raw_ts = ch.get("created_at") or ch.get("release_date") or ""
            if not _raw_ts:
                continue
            try:
                _dt = datetime.fromisoformat(str(_raw_ts).replace("Z", "+00:00"))
                if _dt.tzinfo is None:
                    _dt = _dt.replace(tzinfo=timezone.utc)
                if _dt < _cutoff:
                    continue
            except (ValueError, TypeError):
                continue
            
            _ch_release = ch.get("created_at") or ch.get("release_date") or m.get("latest_chapter_time") or ""
            items.append(build_item(
                title=title, title_key=tk, chapter=ch_str, chapter_num=_chn,
                url=chapter_url, source="shinigami", cover=cover, series_url=series_url,
                chapter_url=chapter_url, origin=origin, updated_time=_ch_release,
                release_date=_ch_release, rating=rating, genres=genres,
                description=description, type=_type2,
            ))
    
    return items
