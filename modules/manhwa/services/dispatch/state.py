"""Shared state for the dispatch send pass.

`_RunState` and the per-send outcome constants live here so both `runner.py`
(orchestration) and `senders.py` (the send path) can import them without a
circular dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Outcomes of one send attempt.
_SENT = "sent"  # delivered (or timeout sentinel — unknown but parked)
_DRY = "dry"  # dry run, counted as would-send
_SKIPPED = "skipped"  # filtered/deduped/failed once, keep going
_BLOCKED = "blocked"  # channel hit the burst threshold, stop this feed


@dataclass
class _RunState:
    """Immutable-ish inputs plus mutable bookkeeping shared by every channel."""

    instance_id: str
    dry_run: bool
    skip_ceiling: bool
    sm_map: dict
    wl_cover_map: dict
    ceilings: dict
    counts: dict
    sent_keys_global: set
    claimed_keys: set
    claimed_urls: set
    legacy_pairs: set
    acq_map: dict
    sent_urls: set = field(default_factory=set)
