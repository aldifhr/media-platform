"""Shared HTTP fetcher.

- simple_get(): plain requests (drakorasia — no Cloudflare).
- cf_get():     curl_cffi impersonate=chrome (pahe.ink / samehadaku CF-protected).

Both return decoded HTML text. Falls back gracefully if curl_cffi missing.
"""
from __future__ import annotations

import os

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def simple_get(url: str, timeout: int = 20, headers: dict | None = None) -> str:
    import requests
    h = {"User-Agent": DEFAULT_UA}
    if headers:
        h.update(headers)
    r = requests.get(url, headers=h, timeout=timeout)
    r.raise_for_status()
    return r.text


_CFFI_AVAILABLE = False
try:
    from curl_cffi import requests as cffi_req
    _CFFI_AVAILABLE = True
except Exception:
    cffi_req = None


def cf_get_json(url: str, timeout: int = 30, impersonate: str = "chrome") -> dict | list:
    """Fetch a Cloudflare-protected JSON endpoint (e.g. WP REST API)."""
    if not _CFFI_AVAILABLE:
        raise RuntimeError("curl_cffi not installed; run: pip install curl_cffi")
    r = cffi_req.get(url, impersonate=impersonate, timeout=timeout,
                     headers={"User-Agent": DEFAULT_UA})
    r.raise_for_status()
    return r.json()


def cf_get(url: str, timeout: int = 30, impersonate: str = "chrome") -> str:
    """Fetch a Cloudflare-protected HTML page via curl_cffi browser impersonation."""
    if not _CFFI_AVAILABLE:
        raise RuntimeError("curl_cffi not installed; run: pip install curl_cffi")
    r = cffi_req.get(url, impersonate=impersonate, timeout=timeout,
                     headers={"User-Agent": DEFAULT_UA})
    r.raise_for_status()
    return r.text


def get(url: str, cf: bool = False, timeout: int = 20, impersonate: str = "chrome") -> str:
    """Convenience: cf=True -> cf_get, else simple_get."""
    if cf:
        return cf_get(url, timeout=timeout, impersonate=impersonate)
    return simple_get(url, timeout=timeout)
