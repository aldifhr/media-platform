"""Whitelist service — CRUD + metadata joins.

Heavy logic lives in app/services/whitelist/:
  * `query.py`     — pagination, metadata joins, response mapping and dedup
  * `mutations.py` — create/delete/patch and stale-row cleanup
  * `normalize.py` — title-key/URL normalization and upstream enrichment

This module stays as the compatibility facade: existing call sites and tests
import from `app.services.whitelist_service`.
"""

from __future__ import annotations

from modules.manhwa.services.dispatch.history import get_dispatch_history  # noqa: F401
from modules.manhwa.services.whitelist.mutations import (  # noqa: F401
    auto_cleanup_stale_whitelist,
    delete_whitelist,
    patch_whitelist,
    post_whitelist,
)
from modules.manhwa.services.whitelist.normalize import (  # noqa: F401
    _derive_title_key_from_url,
    enrich_whitelist_entry,
    normalize_whitelist_urls,
)
from modules.manhwa.services.whitelist.enrich import _fetch_whitelist_enrichment  # noqa: F401
from modules.manhwa.services.whitelist.query import (  # noqa: F401
    _fetch_whitelist_rows,
    build_whitelist_mapped_row,
    dedup_whitelist,
    get_whitelist,
)
from modules.manhwa.storage.whitelist import (  # noqa: F401
    load_whitelist,
    load_whitelist_strict,
)

__all__ = [
    "get_dispatch_history",
    "get_whitelist",
    "post_whitelist",
    "delete_whitelist",
    "patch_whitelist",
    "normalize_whitelist_urls",
    "auto_cleanup_stale_whitelist",
    "load_whitelist",
    "load_whitelist_strict",
]
