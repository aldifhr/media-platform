"""Shared file-backed seen-state store (JSON).

Thread-safe, atomic write, auto-prune. Used by all scrapers so they don't
each reinvent dedup. Key is scraper-defined (slug, url, post id...).

Usage:
    from modules.media.common.state import State
    st = State("pahe")               # -> data/pahe_seen.json
    if st.is_seen(key): ...
    st.mark_seen(key, value={"published": "..."})
    st.prune(days=2)
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from datetime import datetime, UTC
from pathlib import Path

# Reentrant: mark_seen() holds the lock and then calls _save(), which takes it
# again. A plain Lock deadlocked there.
_LOCK = threading.RLock()
_CACHE: dict[str, dict] = {}  # name -> loaded state dict


def _default_dir() -> str:
    # PAHE_STATE_DIR is the documented knob (and what the tests set); STATE_DIR
    # is kept as an alias so nothing that exported it breaks.
    for name in ("PAHE_STATE_DIR", "STATE_DIR"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    # media-scrapers/data/<name>_seen.json
    return str(Path(__file__).parent.parent / "data")


class State:
    """Seen-state for one source, backed by ``<dir>/<name>_seen.json``.

    ``dir`` is resolved on every access rather than cached at construction:
    the module-level ``SEEN`` singleton is built at import time, and a cached
    path would pin it to whatever ``PAHE_STATE_DIR`` happened to be then.
    """

    def __init__(self, name: str, state_dir: str | None = None):
        self.name = name
        self._state_dir = state_dir
        self._data: dict = {}
        self._loaded_path: str | None = None

    @property
    def dir(self) -> str:
        return self._state_dir or _default_dir()

    @property
    def path(self) -> str:
        return os.path.join(self.dir, f"{self.name}_seen.json")

    def _load(self) -> dict:
        path = self.path
        if self._loaded_path == path:
            return self._data
        with _LOCK:
            if self._loaded_path != path:
                self._data = {}
                if os.path.exists(path):
                    try:
                        with open(path, encoding="utf-8") as f:
                            loaded = json.load(f)
                        if isinstance(loaded, dict):
                            self._data = loaded
                    except (OSError, ValueError):
                        self._data = {}
                self._loaded_path = path
            return self._data

    def _save(self) -> None:
        # Write the in-memory view verbatim, under an exclusive lock so a
        # concurrent writer cannot produce a torn file.
        #
        # This used to re-read the file and merge, which silently defeated
        # prune(): a key deleted in memory was still on disk, so the merge put
        # it straight back and the file grew without bound.
        #
        # Single-writer by design — one process owns a state file. The lock
        # guarantees atomicity, not multi-writer merging.
        with _LOCK:
            path = self.path
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            lock_path = path + ".lock"
            with open(lock_path, "w", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    tmp = path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(self._data, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, path)
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def is_seen(self, key: str) -> bool:
        return key in self._load()

    def get(self, key: str, default=None):
        return self._load().get(key, default)

    def mark_seen(self, key: str, value=None) -> None:
        data = self._load()
        now = _now_iso()
        if value is None:
            value = {"seenAt": now}
        elif isinstance(value, dict):
            # ensure a seenAt timestamp exists for prune()
            if "seenAt" not in value:
                value = {**value, "seenAt": now}
        else:
            value = {"value": value, "seenAt": now}
        with _LOCK:
            data[key] = value
            self._save()

    def mark_seen_batch(self, keys: list[str], value=None) -> None:
        data = self._load()
        now = _now_iso()
        with _LOCK:
            for k in keys:
                if isinstance(value, dict):
                    data[k] = {**value, "seenAt": value.get("seenAt", now)}
                else:
                    data[k] = {"value": value, "seenAt": now}
            self._save()

    def count(self) -> int:
        return len(self._load())

    def prune(self, days: int = 7) -> int:
        """Drop entries seen more than `days` ago (value has seenAt ISO)."""
        data = self._load()
        cutoff = time.time() - days * 86400
        cleaned = 0
        with _LOCK:
            for k in list(data.keys()):
                v = data[k]
                ts = _to_ts(v["seenAt"] if isinstance(v, dict) and "seenAt" in v else None)
                if ts and ts < cutoff:
                    del data[k]
                    cleaned += 1
            if cleaned:
                self._save()
        return cleaned


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _to_ts(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0
