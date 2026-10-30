"""FCFS — single source of truth for title+chapter deduplication.

The pure key primitives live in app/core/fcfs.py (so storage can use them too);
this module re-exports them and adds the DB-backed claimed_fcfs_keys helper.
Previously scattered:
- app/cron/dispatch_mod.py: normalize_title, _norm_chapter, fcfs_key, _claimed_titles
- app/storage/dispatch.py: _claimed_fcfs_keys, _claimed_urls
- app/cron/collect.py: _parse_chapter_num
- app/utils/text.py: normalize_title_key (different impl)

All callers should import from here. Re-exports are kept in the old modules for
backward compat.
"""
from __future__ import annotations

from modules.manhwa.logger import get_logger
from modules.manhwa.core.fcfs import (  # noqa: F401
    fcfs_key,
    fcfs_key_for_item,
    normalize_chapter,
    normalize_title,
    parse_chapter_number,
)

logger = get_logger("services:fcfs")

# --- DB helpers --------------------------------------------------------------

# claimed_fcfs_keys now lives in storage (it queries dispatch tables).
from modules.manhwa.storage.dispatch import _claimed_fcfs_keys as claimed_fcfs_keys  # noqa: E402,F401
