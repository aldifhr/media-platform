"""Translate text to Indonesian for the Discord embed.

LibreTranslate was the intended backend, but every public instance is dead
(403/502/000, or "visit portal.libretranslate.com for an API key"). MyMemory
is used instead: free, no API key, anonymous quota ~5k words/day/IP, and it
returns natural Indonesian (verified against pahe synopses).

If you ever self-host LibreTranslate, set TRANSLATE_URL and this module
speaks that protocol instead — same function, no call-site changes.

The cache is held in memory and flushed to disk lazily. It used to re-read and
re-write the entire JSON file on every single translation, so a 16-post batch
paid 16 full file reads and up to 16 full file writes for a cache that is
written once and read once.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import os
import threading

import requests

from modules.media.common.env import env_str, load_env
from modules.media.common.log import get_logger

load_env()

log = get_logger("translate")

_MEMORY_URL = "https://api.mymemory.translated.net/get"
_TIMEOUT = 20
_MAX_ENTRIES = 2000  # hard cap on cached translations
_FLUSH_EVERY = 25  # new translations between disk writes

# One shared Session: keep-alive across the sequential synopsis translations
# a batch notify makes (fresh requests.* per call paid TLS+TCP each time).
# requests.Session is not documented as thread-safe, and translations run on
# worker threads via asyncio.to_thread, so every use is serialized.
_session = requests.Session()
_http_lock = threading.Lock()

# MyMemory mis-reads "viral tragedy" as a viral-disease tragedy and emits
# "tragedi virus". Corrections are applied to the Indonesian OUTPUT, since
# the mistranslation only exists there — matching the English source would
# never fire. Keeps proper nouns (character names) intact.
_GLOSSARY = {
    "tragedi virus": "tragedi viral",
}

_cache: dict[str, str] = {}
_cache_lock = threading.Lock()
_dirty = 0
_loaded = False


def _libretranslate_url() -> str:
    return env_str("TRANSLATE_URL")


def _cache_path() -> str:
    directory = env_str("PAHE_STATE_DIR") or "./data"
    return os.path.join(directory, "translated.json")


def _load_cache() -> None:
    global _loaded
    if _loaded:
        return
    with _cache_lock:
        if _loaded:
            return
        try:
            with open(_cache_path(), encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _cache.update({str(k): str(v) for k, v in data.items()})
        except (OSError, ValueError):
            pass
        _loaded = True


def _flush(force: bool = False) -> None:
    """Persist the cache. ``force`` ignores the write-behind threshold."""
    global _dirty
    with _cache_lock:
        if not _dirty and not force:
            return
        snapshot = dict(_cache)
        _dirty = 0
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("cache write failed: %s", exc)


def _remember(key: str, value: str) -> None:
    global _dirty
    with _cache_lock:
        if key in _cache:
            return
        _cache[key] = value
        _dirty += 1
        if len(_cache) > _MAX_ENTRIES:
            # Synopses are one-shot; drop the oldest quarter rather than grow.
            for old in list(_cache)[: _MAX_ENTRIES // 4]:
                _cache.pop(old, None)
    if _dirty >= _FLUSH_EVERY:
        _flush()


atexit.register(_flush, True)


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _translate_mymemory(text: str) -> str:
    with _http_lock:
        response = _session.get(
            _MEMORY_URL, params={"q": text, "langpair": "en|id"}, timeout=_TIMEOUT
        )
    response.raise_for_status()
    translated = (response.json().get("responseData") or {}).get("translatedText", "").strip()
    if not translated:
        raise ValueError("MyMemory returned no translation")
    return translated


def _translate_libretranslate(text: str) -> str:
    base = _libretranslate_url().rstrip("/")
    # Self-hosted LibreTranslate mounts the API at the root; hosted mirrors
    # sometimes expose it under /translate already.
    url = base if base.endswith("/translate") else f"{base}/translate"
    with _http_lock:
        response = _session.post(
            url,
            data={"q": text, "source": "en", "target": "id", "format": "text"},
            timeout=_TIMEOUT,
        )
    response.raise_for_status()
    translated = (response.json().get("translatedText") or "").strip()
    if not translated:
        raise ValueError("LibreTranslate returned no translation")
    return translated


def translate_to_id(text: str) -> str:
    """Translate English text to Indonesian. Returns "" on failure.

    Callers must treat "" as "keep the original" — a missing translation
    must never cost us the notification.
    """
    text = (text or "").strip()
    if not text:
        return ""

    _load_cache()
    key = _key(text)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    try:
        translated = (
            _translate_libretranslate(text) if _libretranslate_url() else _translate_mymemory(text)
        )
    except Exception as exc:
        log.warning("translation failed: %s: %s", type(exc).__name__, exc)
        return ""

    for english, indonesian in _GLOSSARY.items():
        translated = translated.replace(english, indonesian)

    _remember(key, translated)
    return translated


async def atranslate_to_id(text: str) -> str:
    return await asyncio.to_thread(translate_to_id, text)
