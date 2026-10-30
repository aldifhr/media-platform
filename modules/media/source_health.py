"""Source-health tracker + auto-quarantine.

A source with >=3 consecutive failures gets ``disabled_until`` set 30 min out,
so a check skips it instead of burning a full scrape pass every tick. Recovers
automatically when the cooldown passes. State lives in
``<PAHE_STATE_DIR>/source_health.json`` (no DB needed here).

The health file used to be pinned to ``<repo>/data/`` regardless of
``PAHE_STATE_DIR``, so tests that redirected state to a tmp dir still wrote
into the repository — and a production run with a custom state dir split its
health tracking away from its seen-state.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

_LOCK = threading.RLock()
_COOLDOWN_MIN = 30  # quarantine window
_MAX_CONSEC = 3  # strikes before quarantine


def _state_dir() -> str:
    for name in ("PAHE_STATE_DIR", "STATE_DIR"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return str(Path(__file__).resolve().parent / "data")


def _health_file() -> str:
    return os.path.join(_state_dir(), "source_health.json")


def _now() -> datetime:
    return datetime.now(UTC)


def _load() -> dict:
    try:
        with open(_health_file(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(state: dict) -> None:
    path = _health_file()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Write to a temp file in the SAME directory, then atomically replace:
    # os.replace is only atomic within a filesystem.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".source_health.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise


def should_skip(name: str) -> tuple[bool, str | None]:
    """Return (skip?, reason) for a source based on active quarantine."""
    with _LOCK:
        row = _load().get(name)
        if not isinstance(row, dict):
            return False, None
        disabled_until = row.get("disabled_until")
        if not disabled_until:
            return False, None
        try:
            until = datetime.fromisoformat(str(disabled_until).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return False, None
        if until > _now():
            return True, f"quarantined until {until.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        return False, None


def record(name: str, ok: bool, error: str | None = None) -> None:
    """Update consecutive-failure count + apply quarantine on 3rd strike."""
    with _LOCK:
        state = _load()
        row = state.get(name)
        row = row if isinstance(row, dict) else {}
        consecutive = int(row.get("consecutive_failures", 0) or 0)
        consecutive = 0 if ok else consecutive + 1

        now = _now()
        disabled_until = row.get("disabled_until")
        reason = row.get("last_error")

        if consecutive >= _MAX_CONSEC:
            # Only push the cooldown forward if none is active or it expired.
            # Compare as datetimes, not strings: ISO strings only sort
            # correctly when precision and offset match exactly.
            active_until = _parse(disabled_until)
            if active_until is None or active_until <= now:
                disabled_until = (now + timedelta(minutes=_COOLDOWN_MIN)).isoformat()
                reason = error or f"auto-quarantined after {consecutive} consecutive failures"

        state[name] = {
            "status": "healthy" if ok else "degraded",
            "consecutive_failures": consecutive,
            "disabled_until": disabled_until,
            "last_error": None if ok else (error or reason),
            "last_checked_at": now.isoformat(),
        }
        _save(state)


def snapshot() -> dict:
    """Per-source health for /health or diagnostics."""
    with _LOCK:
        return _load()


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
