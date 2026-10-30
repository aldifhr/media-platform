"""Shared HTTP client for Discord cover fetching.

Reuse a single httpx.Client per process instead of one per chapter to cut
connection overhead and socket churn when many chapters send in one run.
"""
from __future__ import annotations

import httpx


class _CoverClient:
    """Lazy-initialized, process-wide httpx client for cover images."""

    _instance: httpx.Client | None = None

    @classmethod
    def get(cls) -> httpx.Client:
        if cls._instance is None:
            cls._instance = httpx.Client(
                timeout=15,
                headers={"User-Agent": "Discordbot"},
                follow_redirects=True,
            )
        return cls._instance

    @classmethod
    def close(cls) -> None:
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None


def _is_cover_allowed(url: str) -> bool:
    """Allowlist check — reuse PROXY_ALLOWED_HOSTS. Prevent SSRF to internal/metadata."""
    try:
        from urllib.parse import urlparse

        from modules.manhwa.config import settings

        p = urlparse(url)
        host = (p.hostname or "").strip().lower()
        port = p.port or (443 if p.scheme == "https" else 80)
        allowed = getattr(settings, "PROXY_ALLOWED_HOSTS", [])
        return p.scheme in ("http", "https") and f"{host}:{port}" in allowed
    except Exception:
        return False


def _resize_cover(data: bytes, content_type: str) -> tuple[bytes, str]:
    """Downscale oversized covers so Discord renders the attachment thumbnail.

    Discord silently drops attachment:// thumbnails when the uploaded file
    is very large — observed: a 1.4MB PNG (960x1246) uploaded fine but the
    embed rendered with no image, while a 95KB webp rendered correctly.
    Resize to max 512px on the long edge and re-encode as JPEG (smallest
    reliable format for Discord).
    """
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        max_dim = 512
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            new_size = (round(img.width * ratio), round(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        out = buf.getvalue()
        # Only use the resized version if it's actually smaller
        if len(out) < len(data):
            return out, "image/jpeg"
    except Exception:
        pass
    return data, content_type


def fetch_cover(url: str) -> tuple[bytes, str] | None:
    """Fetch cover image bytes + content-type. Returns None on any error or SSRF.

    Security: follow_redirects=False — validate each hop manually to prevent
    redirect-based SSRF bypass (e.g., upstream returns 302 → 169.254.169.254).
    """
    if not url or not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    if not _is_cover_allowed(url):
        return None
    try:
        # Do NOT follow redirects — validate each hop manually
        r = _CoverClient.get().get(url, follow_redirects=False)
        # Check for redirect and validate the Location header
        if r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get("location", "")
            if not location or not _is_cover_allowed(location):
                return None
            # Follow manually only if allowed
            r = _CoverClient.get().get(location, follow_redirects=False)
        if r.status_code == 200 and r.content:
            # cap 10MB
            data = r.content[: 10 * 1024 * 1024]
            content_type = r.headers.get("content-type", "image/jpeg")
            # Resize oversized covers so Discord renders the attachment thumbnail
            data, content_type = _resize_cover(data, content_type)
            return (data, content_type)
    except Exception:
        pass
    return None
