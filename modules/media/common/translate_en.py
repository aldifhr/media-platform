"""Russian -> English translation (Google gtx endpoint), best-effort + cached.

Returns "" on failure — callers keep the original text. A missing translation
must never cost a notification.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading

import requests

_GTX_URL = "https://translate.googleapis.com/translate_a/single"
_TIMEOUT = 15
_lock = threading.Lock()
_cache: dict[str, str] = {}
_loaded = False
_CACHE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "media", "translate_en_cache.json")
)


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            _cache.update(json.load(f))
    except (OSError, ValueError):
        pass


def _flush() -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_cache, f)
    except OSError:
        pass


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _call(text: str) -> str:
    r = requests.get(
        _GTX_URL,
        params={"client": "gtx", "sl": "ru", "tl": "en", "dt": "t", "q": text},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    return "".join(part[0] for part in data[0] if part[0]).strip()


def translate_ru_to_en(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    _load()
    key = _key(text)
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    try:
        out = _call(text)
    except Exception:
        return ""
    if not out:
        return ""
    with _lock:
        _cache[key] = out
    _flush()
    return out


_BOILERPLATE = (
    r"\s*играть по сети и интернету онлайн.*$",
    r"\s*играть по сети и интернету.*$",
    r"\s*играть по сети.*$",
    r"\s*по сети.*$",
)


def clean_ru_title(title: str) -> str:
    """Strip the site's repetitive 'play over the network' boilerplate."""
    t = title or ""
    for pat in _BOILERPLATE:
        t = re.sub(pat, "", t, flags=re.I)
    return t.strip() or (title or "").strip()


def clean_dl_label(text: str) -> str:
    """Normalise a download anchor label to a short English tag."""
    label = re.sub(r"^\s*скачать\s+", "", text or "", flags=re.I).strip()
    if not label:
        return "Download"
    if re.search(r"\.(torrent|exe|zip|rar|7z|dmg)", label, re.I):
        return label[:60]
    return (translate_ru_to_en(label) or label)[:60]
