"""Dispatch storage — re-export facade.

Split into:
  * `history.py` — already-sent / already-claimed lookups
  * `claims.py`  — claim/unclaim lifecycle + the atomic claim guard
  * `failed.py`  — failed_dispatches writes

Existing call sites import from `app.storage.dispatch`; the names below are
kept for backward compatibility.
"""
from __future__ import annotations

from modules.manhwa.storage.dispatch.claims import (  # noqa: F401
    claim_and_record,
    complete_dispatch_claim,
    mark_claimed,
    unclaim,
    unclaim_stale,
)
from modules.manhwa.storage.dispatch.failed import record_failed  # noqa: F401
from modules.manhwa.storage.dispatch.history import (  # noqa: F401
    _already_dispatched,
    _claimed_fcfs_keys,
    _claimed_urls,
)

__all__ = [
    "_already_dispatched",
    "_claimed_urls",
    "_claimed_fcfs_keys",
    "mark_claimed",
    "record_failed",
    "unclaim",
    "unclaim_stale",
    "claim_and_record",
    "complete_dispatch_claim",
]
