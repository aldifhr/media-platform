"""OpenAPI customizer — whitelist + RSS pagination params + dual format/country."""
from fastapi.openapi.utils import get_openapi
from modules.manhwa.config import VALID_SOURCES

_openapi_schema = None


def custom_openapi(app):
    global _openapi_schema
    if _openapi_schema:
        return _openapi_schema
    schema = get_openapi(
        title="manhwa-backend",
        version="1.1.0",
        description="Manhwa scanner API. Backend runs fully on local VPS Postgres. Use Bearer token for protected endpoints. RSS feeds support dual-read params: format (manhwa/manhua/manga) + type (legacy alias), country (KR/CN/JP) + origin (legacy alias).",
        routes=app.routes,
    )
    schema["servers"] = [{"url": "https://scanner.aldifhr.my.id", "description": "Production (VPS)"}]
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
    }
    for path in schema.get("paths", {}).values():
        for method in path.values():
            method.setdefault("security", [{"BearerAuth": []}])

    _wl_params = [
        {"name": "page", "in": "query", "required": False, "schema": {"type": "integer", "default": 1, "minimum": 1}, "description": "Page number (1-based)."},
        {"name": "page_size", "in": "query", "required": False, "schema": {"type": "integer", "default": 100, "minimum": 1, "maximum": 10000}, "description": "Rows per page (1..10000)."},
        {"name": "source", "in": "query", "required": False, "schema": {"type": "string", "enum": list(VALID_SOURCES)}, "description": "Filter by source."},
        {"name": "title", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Case-insensitive title search."},
        {"name": "cursor", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Keyset cursor (created_at ISO) for pagination — preferred over page for large tables."},
        {"name": "merge", "in": "query", "required": False, "schema": {"type": "boolean", "default": False}, "description": "Merge cross-source rows into one (groups sources into sources[] array)."},
    ]
    for _p in ("/api/v1/whitelist", "/api/v1/reader/whitelist"):
        _ep = schema.get("paths", {}).get(_p, {}).get("get")
        if _ep is not None:
            _ep["parameters"] = _wl_params

    _rss_params = [
        {"name": "page", "in": "query", "required": False, "schema": {"type": "integer", "default": 1, "minimum": 1}, "description": "Page number (1-based)."},
        {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer", "default": 50, "minimum": 1, "maximum": 100}, "description": "Results per page (1..100)."},
        {"name": "group", "in": "query", "required": False, "schema": {"type": "boolean", "default": True}, "description": "Group chapters by (titleKey, source)."},
        {"name": "format", "in": "query", "required": False, "schema": {"type": "string", "enum": ["manhwa", "manhua", "manga"]}, "description": "Filter by format (manhwa/manhua/manga). Dual-read: accepts 'type' as legacy alias."},
        {"name": "type", "in": "query", "required": False, "schema": {"type": "string", "enum": ["manhwa", "manhua", "manga"]}, "description": "Legacy alias for format. Prefer 'format'."},
        {"name": "country", "in": "query", "required": False, "schema": {"type": "string", "enum": ["KR", "CN", "JP"]}, "description": "Filter by country of origin. Dual-read: accepts 'origin' as legacy alias."},
        {"name": "origin", "in": "query", "required": False, "schema": {"type": "string", "enum": ["KR", "CN", "JP"]}, "description": "Legacy alias for country. Prefer 'country'."},
        {"name": "source", "in": "query", "required": False, "schema": {"type": "string", "enum": list(VALID_SOURCES)}, "description": "Filter by source."},
        {"name": "exclude", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Exclude country codes (comma-separated, e.g. JP)."},
        {"name": "exclude_origin", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Legacy alias for exclude."},
        {"name": "q", "in": "query", "required": False, "schema": {"type": "string", "maxLength": 100}, "description": "Search title (case-insensitive ILIKE)."},
        {"name": "genres", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Filter by genre (comma-separated)."},
        {"name": "min_rating", "in": "query", "required": False, "schema": {"type": "number", "minimum": 0, "maximum": 10}, "description": "Minimum rating filter."},
        {"name": "max_rating", "in": "query", "required": False, "schema": {"type": "number", "minimum": 0, "maximum": 10}, "description": "Maximum rating filter."},
        {"name": "subscribed_only", "in": "query", "required": False, "schema": {"type": "boolean", "default": False}, "description": "Only show whitelisted series."},
        {"name": "sort", "in": "query", "required": False, "schema": {"type": "string", "enum": ["newest", "rating", "popular"], "default": "newest"}, "description": "Sort order."},
        {"name": "whitelist", "in": "query", "required": False, "schema": {"type": "boolean", "default": False}, "description": "Only whitelisted (same as subscribed_only)."},
        {"name": "exclude_notified", "in": "query", "required": False, "schema": {"type": "boolean", "default": False}, "description": "Exclude chapters already dispatched."},
        {"name": "unread_only", "in": "query", "required": False, "schema": {"type": "boolean", "default": False}, "description": "Exclude already-sent chapters."},
    ]
    for _p in ("/api/v1/rss", "/api/v1/reader/rss"):
        _ep = schema.get("paths", {}).get(_p, {}).get("get")
        if _ep is not None:
            _ep["parameters"] = _rss_params

    _openapi_schema = schema
    return schema
