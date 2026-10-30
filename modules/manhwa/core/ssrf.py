"""SSRF guard — allowlist outbound URLs + block private IPs."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# harus dibatasi ke allowed domains, jangan 127.0.0.1 / 169.254.169.254 / localhost
ALLOWED_HOSTS = {
    "shinigami.asia", "11.shinigami.asia", "f.shinigami.asia", "api.shngm.io", "assets.shngm.id",
    "ikiru.wtf", "cdn.ikiru.id",
    "discord.com", "cdn.discordapp.com",
    "imgkc1.my.id", "minio.imgkc1.my.id",
    "scanner.aldifhr.my.id", "manhwa.aldifhr.my.id",
}

# suffix match for *.shinigami.asia
ALLOWED_SUFFIXES = (".shinigami.asia",)

def _is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        # not IP literal → check hostname
        h = host.lower()
        if h in ("localhost", "metadata.google.internal"):
            return True
        if h.startswith("169.254.") or h.startswith("127.") or h.startswith("10.") or h.startswith("192.168."):
            return True
        return False

def assert_allowed_url(url: str) -> None:
    if not url or not isinstance(url, str):
        raise ValueError("URL empty")
    if len(url) > 2000:
        raise ValueError("URL too long")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme not allowed: {parsed.scheme}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL without host")
    if _is_private_ip(host):
        raise ValueError(f"private IP blocked: {host}")
    # allowlist check
    if host in ALLOWED_HOSTS:
        return
    for suffix in ALLOWED_SUFFIXES:
        if host.endswith(suffix):
            return
    # also allow IP-less but known CDN hosts via suffix
    raise ValueError(f"host not allowlisted: {host}")

def is_allowed_url(url: str) -> bool:
    try:
        assert_allowed_url(url)
        return True
    except Exception:
        return False
