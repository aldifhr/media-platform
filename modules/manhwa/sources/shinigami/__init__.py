"""Shinigami (secondary source) API client.

Shinigami exposes a public REST API at api.shngm.io/v1 that does NOT sit behind
Cloudflare, so a plain HTTP client works. Parity with
shared/scrapers/secondary/api.ts.
"""
import httpx
import random
import time as _t
from datetime import datetime, timezone, timedelta

from curl_cffi import requests as cffi_req

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger
from modules.manhwa.core.resilience import cb_shinigami

from modules.manhwa.core.rating_utils import normalize_rating
from modules.manhwa.sources.shinigami.models import ShinigamiLatestResponse, ShinigamiDetailResponse
logger = get_logger("shinigami:api")

# Lazy BASE/API so tests can patch settings.SECONDARY_SOURCE_URL at runtime (was import-time binding)
def _base() -> str:
    return settings.SECONDARY_SOURCE_URL.rstrip("/")
def _api() -> str:
    return f"{_base()}/v1"
def _public() -> str:
    return settings.SECONDARY_PUBLIC_BASE.rstrip("/")
# Keep module-level constants for backward compat (callers that import BASE/API)
BASE = _base()  # type: ignore
API = _api()  # type: ignore
PUBLIC = _public()  # type: ignore
# Shinigami's API answers in ~290ms normally. 10s was the ceiling for the
# httpx attempt, so a hung socket stalled the whole collect for a full 10
# seconds before the fallback ran. 4s is still 13x the normal latency and
# keeps one slow endpoint from eating the 2-minute pipeline budget.
TIMEOUT = 4.0

_HEADERS = {
    "User-Agent": settings.HTTP_USER_AGENT,
    "Accept": "application/json",
}

# PERF-03: reuse a single httpx.Client across all requests so TCP/TLS
# connections are pooled + keep-alive'd instead of re-handshaking on every
# _get() call. httpx.Client is thread-safe (internal connection pool).
_CLIENT = httpx.Client(timeout=TIMEOUT, headers=_HEADERS, verify=True)

def _fetch(url: str):
    """Fetch URL, falling back to curl_cffi when httpx cannot get through.

    The curl_cffi fallback must cover BOTH non-200 responses AND transport
    failures. It previously only ran on a status code, so an httpx
    ConnectTimeout/ReadTimeout raised out of the call and the whole path
    was abandoned — no curl_cffi attempt at all. Measured live: the second
    shinigami collect in each cycle took ~10.3s (exactly TIMEOUT=10.0)
    while the first took ~290ms. httpx was hanging until the deadline and
    curl_cffi, which succeeds where httpx is TLS-fingerprinted, was never
    tried. Retry the whole fetch once via curl_cffi on any transport error;
    the caller's own retry loop handles repeated failures.
    """
    try:
        r = _CLIENT.get(url)
    except Exception as e:
        logger.debug("shinigami httpx transport error, trying curl_cffi", err=str(e)[:120])
        return cffi_req.get(url, headers=_HEADERS, impersonate="chrome", timeout=TIMEOUT)
    if r.status_code == 200:
        return r
    logger.debug("shinigami httpx blocked, trying curl_cffi", status=r.status_code)
    return cffi_req.get(url, headers=_HEADERS, impersonate="chrome", timeout=TIMEOUT)


def _get(path: str, retries: int = 4):
    from modules.manhwa.core.ssrf import assert_allowed_url

    assert_allowed_url(f"{API}{path}")
    if not cb_shinigami.allow():
        logger.debug("shinigami circuit OPEN — skipping fetch", path=path)
        return None
    try:
        for attempt in range(retries + 1):
            r = _fetch(f"{API}{path}")
            if r.status_code == 200:
                cb_shinigami.record_success()
                return r.json()
            if r.status_code in (429, 403, 500, 502, 503) and attempt < retries:
                # R1/R2 FIX: True jittered backoff + respect Retry-After
                retry_after = r.headers.get("retry-after")
                if retry_after:
                    try:
                        _sleep = float(retry_after)
                    except (ValueError, TypeError):
                        _sleep = min(3.0 * (attempt + 1), 12.0) + random.uniform(0, 1.0)
                else:
                    _sleep = min(3.0 * (attempt + 1), 12.0) + random.uniform(0, 1.0)
                _t.sleep(_sleep)
                continue
            logger.debug("Shinigami HTTP error", path=path, status=r.status_code)
            cb_shinigami.record_failure()
            return None
    except Exception as e:
        cb_shinigami.record_failure()
        logger.debug("Shinigami fetch failed", path=path, err=str(e))
    return None

def search_shinigami_api(query: str, per_page: int = 20):
    q = query.replace("/", " ")
    data = _get(f"/manga/list?q={q}&page=1&page_size={per_page}")
    if isinstance(data, dict):
        return data.get("data", []) or []
    if isinstance(data, list):
        return data
    return []

def get_shinigami_latest_updates(page: int = 1, per_page: int = 100, max_pages: int = 10, hours_cutoff: int = 24):
    """Fetch latest-updates across BOTH manga types (mirror + project).

    Stop kalau oldest di page udah lewat cutoff, biar gak miss kalau
    update >24 dalam 24 jam tapi juga gak boros fetch 10 page terus kalau cuma 1 page fresh.
    """
    from datetime import datetime, timezone, timedelta

    try:
        _cutoff = datetime.now(timezone.utc) - timedelta(hours=int(hours_cutoff or 24))
    except Exception:
        _cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    def _is_fresh(item: dict) -> bool:
        # latest_chapter_time adalah waktu chapter terbaru (paling akurat untuk early-stop)
        # fallback ke updated_at/created_at kalau field missing
        ts_raw = item.get("latest_chapter_time") or item.get("updated_at") or item.get("created_at") or ""
        if not ts_raw:
            return False
        try:
            dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt >= _cutoff
        except Exception:
            return False

    all_items: list[dict] = []
    seen_ids: set[str] = set()
    
    # Fetch with is_update filter (fast, but may be cached/stale) — early-stop kalau page udah gak fresh
    for mtype in ("mirror", "project"):
        for p in range(1, max_pages + 1):
            data = _get(f"/manga/list?type={mtype}&page={p}&page_size={per_page}&is_update=true&sort=latest&sort_order=desc")
            if not data:
                break
            try:
                items = ShinigamiLatestResponse.model_validate(data).model_dump().get("data", [])
            except Exception:
                break
            if not items:
                break
            # simpan dulu (dedup), tapi cek fresh untuk early-stop
            has_fresh = False
            for it in items:
                if _is_fresh(it):
                    has_fresh = True
                mid = it.get("manga_id")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    all_items.append(it)
            # kalau di page ini udah gak ada yg fresh, page selanjutnya pasti lebih tua (sort latest) -> stop
            if not has_fresh:
                break
            if len(items) < per_page:
                break
    
    return all_items

def get_shinigami_series(manga_id: str):
    data = _get(f"/manga/detail/{manga_id}")
    if not data:
        return None
    try:
        return ShinigamiDetailResponse.model_validate(data).data
    except Exception as exc:
        cb_shinigami.record_failure()
        logger.warn("Shinigami detail schema invalid", manga_id=manga_id, err=str(exc)[:200])
        raise RuntimeError("Shinigami detail schema invalid") from exc

def _country_to_type(country_id: str | None) -> str | None:
    """Map shinigami country_id to content type: KR->manhwa, CN->manhua, JP->manga."""
    if not country_id:
        return None
    mapping = {"KR": "manhwa", "CN": "manhua", "JP": "manga"}
    return mapping.get(country_id.upper())

def get_shinigami_series_meta(manga_id: str) -> dict | None:
    """Normalize shinigami series detail into a flat metadata dict for
    enriching whitelist / whitelist. Extracts cover, rating, genres,
    description, author, artist, status, type, released from the rich
    /v1/manga/detail/<id> taxonomy. Returns None on failure.
    """
    d = get_shinigami_series(manga_id)
    if not d:
        return None
    tax = d.get("taxonomy") or {}
    # taxonomy can be a LIST in some API responses (whitelist.py handles both
    # shapes) — a dict .get() on a list would AttributeError and kill the
    # whole cron prefetch.
    if not isinstance(tax, dict):
        tax = {}
    genres = [g.get("name") for g in (tax.get("Genre") or []) if g.get("name")]
    authors = [a.get("name") for a in (tax.get("Author") or []) if a.get("name")]
    artists = [a.get("name") for a in (tax.get("Artist") or []) if a.get("name")]
    formats = [f.get("name") for f in (tax.get("Format") or []) if f.get("name")]
    types = [t.get("name") for t in (tax.get("Type") or []) if t.get("name")]
    desc = (d.get("description") or "").strip()
    import re as _re
    import html as _html
    desc_clean = _re.sub(r"<[^>]+>", "", desc)
    desc_clean = _html.unescape(desc_clean)
    desc_clean = _re.sub(r"\s+", " ", desc_clean).strip()
    return {
        "cover": d.get("cover_image_url") or d.get("cover_portrait_url"),
        "rating": normalize_rating(d.get("user_rate") if d.get("user_rate") is not None else d.get("rating")),
        "genres": genres,
        "description": desc_clean[:2000],
        "author": ", ".join(authors) if authors else None,
        "artist": ", ".join(artists) if artists else None,
        "type": _country_to_type(d.get("country_id")) or (formats or types or [""])[0].lower() if (formats or types) else None,
        "released": str(d.get("release_year") or ""),
        "origin": d.get("country_id"),
        "source": "shinigami",
        "series_url": f"{settings.SHINIGAMI_PUBLIC_BASE}{settings.SHINIGAMI_SERIES_PATH}{manga_id}",
    }

def get_shinigami_chapters(
    manga_id: str,
    per_page: int = 100,
    max_pages: int = 5,
    within_hours: int | None = None,
) -> list[dict]:
    """Fetch chapter list for a shinigami manga. Returns list of
    dicts with chapter_number and chapter_id. Alias of the
    canonical fetcher (preserved for callers in gap_detector,
    backfill scripts).

    max_pages bounds pagination. The default (5) keeps the historical
    full-archive behaviour for callers that need it (gap_detector,
    catalog).

    within_hours switches to adaptive pagination: keep fetching pages
    only while chapters are still inside the freshness window, and stop as
    soon as a page comes back entirely outside it. The endpoint sorts by
    chapter_number desc, so chapters outside the window always sit behind
    the fresh ones — no need to guess a page count. max_pages remains a
    hard ceiling so a series that dumps a huge batch in one window still
    terminates.
    """
    all_ch: list[dict] = []
    seen_ids: set[str] = set()
    cutoff: datetime | None = None
    if within_hours is not None and within_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)

    def _is_fresh(ch: dict) -> bool:
        """True if the chapter is inside the window (or undated -> keep)."""
        if cutoff is None:
            return True
        raw = ch.get("release_date") or ch.get("created_at") or ""
        if not isinstance(raw, str) or not raw.strip():
            return True  # undated: never drop on missing data
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return True
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts >= cutoff

    for page in range(1, max(1, max_pages) + 1):
        data = _get(f"/chapter/{manga_id}/list?page={page}&page_size={per_page}&sort_by=chapter_number&sort_order=desc")
        if not data:
            break
        items = data.get("data", [])
        if not items:
            break
        # Freshness scan: every chapter on this page is outside the window,
        # and the list is sorted desc, so nothing deeper can be fresh.
        if cutoff is not None and not any(_is_fresh(ch) for ch in items):
            break
        new_count = 0
        for ch in items:
            ch_id = ch.get("chapter_id") or ch.get("id")
            if ch_id and ch_id not in seen_ids:
                seen_ids.add(ch_id)
                all_ch.append(ch)
                new_count += 1
        if new_count == 0 or len(items) < per_page:
            break
    return all_ch
