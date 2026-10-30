"""Ikiru API client.

Ikiru exposes a public REST API at 09.ikiru.wtf/api/public that does NOT sit
behind Cloudflare, so a plain HTTP client works.

Endpoint: /api/public/manga/project?page=1&layout=vertical&limit=24
Returns: data.project[] with id, slug, title, featuredImage, type,
         metadata (genre[], score), chapter[] (number, updatedAt)

Only 1 page is needed — the API returns 24 series per page and the total
catalogue is ~146 series. The collector walks page 1 only.
"""
from __future__ import annotations

import httpx

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger
from modules.manhwa.core.resilience import cb_ikiru

logger = get_logger("ikiru:api")

TIMEOUT = 10.0
PAGE_SIZE = 24

_HEADERS = {
    "User-Agent": settings.HTTP_USER_AGENT,
    "Accept": "application/json",
}

_client: httpx.Client | None = None


def _api() -> str:
    return settings.IKIRU_API_BASE.rstrip("/")


def _public() -> str:
    return settings.IKIRU_PUBLIC_BASE.rstrip("/")


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=TIMEOUT, headers=_HEADERS, verify=True)
    return _client


@cb_ikiru
def get_ikiru_project_page(page: int = 1, limit: int = PAGE_SIZE) -> dict:
    """Fetch one page of the project manga list.

    Returns the raw JSON dict, or {} on failure.
    """
    url = f"{_api()}/api/public/manga/project"
    params = {"page": page, "layout": "vertical", "limit": limit}
    r = _get_client().get(url, params=params)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        logger.warn("ikiru API returned success=false", message=data.get("message", ""))
        return {}
    return data


def get_ikiru_series_detail(slug: str) -> dict:
    """Fetch full series detail by slug.

    Returns the raw data dict with description, metadata, chapters, etc.
    """
    url = f"{_api()}/api/public/manga/{slug}"
    r = _get_client().get(url)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        return {}
    return data.get("data", {})


def get_ikiru_series_chapters(series_id: str) -> list[dict]:
    """Fetch chapters for a single series.

    Returns a list of chapter dicts with number and updatedAt.
    """
    url = f"{_api()}/api/public/manga/project/{series_id}/chapters"
    r = _get_client().get(url)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        return []
    return data.get("data", {}).get("chapters", [])


def series_url_for(slug: str) -> str:
    return f"{_public()}/manga/{slug}"


def chapter_url_for(slug: str, chapter_number: int | str, chapter_slug: str = "") -> str:
    """Build chapter URL.

    Ikiru uses /manga/{slug}/chapter-{number} (dash, not slash).
    If chapter_slug is provided from the API, use it directly.
    """
    if chapter_slug:
        return f"{_public()}/manga/{slug}/{chapter_slug}"
    return f"{_public()}/manga/{slug}/chapter-{chapter_number}"


def _derive_format(raw: str) -> tuple[str, str]:
    """Map the API's type field onto (type, origin).

    Ikiru only hosts MANHWA titles, so the default is always ("manhwa", "KR").
    """
    key = str(raw or "").strip().lower()
    if not key or key in ("manhwa", "manhwa_scan", "project", "comic"):
        return "manhwa", "KR"
    if key in ("manhua", "manju"):
        return "manhua", "CN"
    if key in ("manga", "novel", "novel_manga"):
        return "manga", "JP"
    return "manhwa", "KR"
