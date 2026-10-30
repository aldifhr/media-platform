"""Single .env loader for media-scrapers.

Every entrypoint used to ship its own copy of this loop, and they disagreed on
one thing that matters: ``media.py`` used ``setdefault`` (real environment
wins) while ``pahe/main.py`` and ``pahe/scanner/main.py`` assigned
unconditionally, so a value in ``.env`` silently clobbered an explicitly
exported variable — including the token the test fixture sets. One loader,
one rule: the real environment always wins.
"""

from __future__ import annotations

import os
from pathlib import Path

_LOADED = False


def repo_root() -> Path:
    """Absolute path of the repository root (parent of ``common/``)."""
    return Path(__file__).resolve().parent.parent


def load_env(path: str | os.PathLike[str] | None = None, *, force: bool = False) -> dict[str, str]:
    """Load ``.env`` into ``os.environ`` without overwriting existing keys.

    Returns the mapping of keys this call actually applied. Idempotent unless
    ``force=True``; safe to call from every module at import time.
    """
    global _LOADED
    if _LOADED and not force:
        return {}

    env_path = Path(path) if path else repo_root() / ".env"
    applied: dict[str, str] = {}
    try:
        raw = env_path.read_text(encoding="utf-8")
    except OSError:
        _LOADED = True
        return applied

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip()
        applied[key] = value.strip()

    _LOADED = True
    return applied


def env_str(name: str, default: str = "") -> str:
    """Read a string setting, treating blank as unset."""
    return (os.environ.get(name) or "").strip() or default


def env_int(name: str, default: int) -> int:
    """Read an int setting, falling back to ``default`` on blank/garbage."""
    raw = env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_list(name: str, *, pattern: str | None = None) -> list[str]:
    """Read a comma-separated setting into a clean list.

    ``pattern`` optionally restricts each item (used for pahe tag slugs, which
    end up in a URL path and must not contain path metacharacters).
    """
    import re

    items = [t.strip() for t in env_str(name).split(",") if t.strip()]
    if pattern:
        ok = re.compile(pattern)
        items = [t for t in items if ok.fullmatch(t)]
    return items
