"""Whitelist storage (parity with lib/services/storage/whitelist.ts)."""
from typing import Optional
import re as _re

from pydantic import BaseModel, Field, field_validator

from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger
from modules.manhwa.core.text import is_uuid_title_key, rewrite_shinigami_urls, slugify_title_key
from modules.manhwa.core.cache import ttl_cache

logger = get_logger("storage:whitelist")

from modules.manhwa.config import VALID_SOURCES, VALID_SOURCES_WITH_ALL

# Use config constants — add a source? One file change in config.py
_VALID_SOURCES = VALID_SOURCES
_VALID_SOURCES_WITH_ALL = VALID_SOURCES_WITH_ALL

class WhitelistRow(BaseModel):
    """Validated + normalized whitelist row.

    The whitelist table is FLAT-per-source (composite PK
    title_key+source). This model is the single source of truth
    for the row *shape* — every read path gets a normalized
    instance (so .get() on a str field can't surprise callers),
    and every write path serializes via to_db() (so a malformed
    dict from the FE / cron can't pollute the DB schema).

    Replaces the previous "raw dict, .get() everywhere" pattern that
    was the root cause of 5 audit bugs (#1-#5): missing fields,
    wrong types, and nested sources[] assumptions that never matched
    the real flat schema.

"ignore" for back-compat, rss falls back sm>it>wl.
    """

    model_config = {"extra": "ignore"}  # tolerate legacy columns

    title_key: str
    source: str
    title: Optional[str] = None
    url: Optional[str] = None
    permalink: Optional[str] = None
    series_url: Optional[str] = None
    cover: Optional[str] = None
    rating: Optional[float] = None
    genres: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    status: Optional[str] = None
    origin: Optional[str] = None
    latest_chapter: Optional[float] = None
    latest_sent_chapter: Optional[float] = None
    created_at: Optional[str] = None

    @field_validator("title_key", mode="before")
    @classmethod
    def _norm_tk(cls, v):
        if not v:
            return ""
        raw = str(v).strip()
        # UUID vs slug vs spaced lower caused merge false / delete mismatches — UUID is legacy
        if is_uuid_title_key(raw):
            logger.warn("WhitelistRow: UUID title_key not canonical, expected slug", title_key=raw[:16])
            return raw.lower()  # keep UUID as-is for DB compat; post_whitelist now resolves UUID→slug via title
        if _re.match(r"^[0-9a-f]{8} [0-9a-f]{4} [0-9a-f]{4} [0-9a-f]{4} [0-9a-f]{12}$", raw, _re.I):
            logger.warn("WhitelistRow: spaced UUID title_key not canonical", title_key=raw[:16])
            return raw.lower().replace(" ", "-")
        # enforce slug: normalize_title_key (alnum+space collapse) then dash — no DB migration, normalize in code
        return slugify_title_key(raw)

    @field_validator("source", mode="before")
    @classmethod
    def _norm_src(cls, v):
        s = str(v or "").strip().lower()
        if s not in _VALID_SOURCES:
            # Tolerate legacy/unknown source but normalize casing.
            return s
        return s

    @field_validator("genres", mode="before")
    @classmethod
    def _coerce_genres(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            # Some rows store "a,b,c"; normalize to list.
            return [g.strip() for g in v.split(",") if g.strip()]
        if isinstance(v, list):
            return [str(g) for g in v if g]
        return []

    def to_db(self) -> dict:
        """Serialize for upsert (only whitelist minimal cols)."""
        d = self.model_dump(exclude_none=True, exclude={"cover", "rating", "genres", "description", "status", "origin", "type", "permalink"})
        # also drop empty list default for genres if somehow included
        d.pop("genres", None)
        d.pop("cover", None)
        d.pop("rating", None)
        d.pop("description", None)
        d.pop("status", None)
        d.pop("origin", None)
        d.pop("type", None)
        # ensure composite PK fields are always present
        d.setdefault("title_key", self.title_key)
        d.setdefault("source", self.source)
        # Backward compat: whitelist.created_at is NOT NULL with default now(),
        # but older call sites/fixtures may omit it.
        if not d.get("created_at"):
            from datetime import datetime, timezone
            d["created_at"] = datetime.now(timezone.utc).isoformat()
        return d

# Cache decorator applied to load_whitelist() below.

def _norm_row(r: dict) -> dict:
    out = dict(r)
    return rewrite_shinigami_urls(out, ("url", "permalink", "cover"))

def _query_whitelist() -> list[dict]:
    """One uncached read of the whitelist table. Raises on failure."""
    res = (
        get_supabase()
        .table("whitelist")
        .select("title_key, source, series_url, latest_sent_chapter, title, id, url, latest_chapter, created_at")
        .limit(5000)
        .execute()
    )
    rows = res.data or []
    out = []
    for r in rows:
        try:
            out.append(_norm_row(r))
        except Exception as e:
            logger.warn("load_whitelist: _norm_row failed, using raw row", err=str(e)[:120])
            out.append(r)
    return out


@ttl_cache(ttl=600.0, maxsize=1)
def load_whitelist(force: bool = False) -> list[dict]:
    """Cached whitelist read for DISPLAY paths — degrades to [] on error."""
    try:
        return _query_whitelist()
    except Exception as e:
        logger.error("Failed to load whitelist", exc=e)
        # The failure is never cached: the wrapper only stores a value once
        # fn() returns, so a raised error leaves the previous snapshot in
        # place instead of poisoning the cache with [] — which means "nothing
        # is subscribed" and routes every chapter to RAW for the full TTL.
        return []


def load_whitelist_strict() -> list[dict]:
    """Whitelist read for ROUTING decisions — raises instead of degrading.

    load_whitelist() collapses "nothing is subscribed" and "the subscriptions
    could not be read" into the same empty list. Routing has to tell those
    apart: announcing on an unreadable whitelist pushes subscribed chapters
    onto the RAW feed. Callers skip the tick when this raises.
    """
    return _query_whitelist()

def get_whitelist_entry(title_key: str, source: str) -> Optional[dict]:
    """One whitelist row by its composite PK, read straight from the DB.

    Deliberately NOT cached: the caller uses this to distinguish "already
    subscribed" from "newly added", and the 600s load_whitelist() snapshot would
    answer from before the write. Returns None when the row is absent or the
    lookup fails — a failed check must not block the write.
    """
    if not title_key or not source:
        return None
    try:
        rows = (
            get_supabase()
            .table("whitelist")
            .select("title_key, source, title, series_url")
            .eq("title_key", str(title_key))
            .eq("source", str(source))
            .limit(1)
            .execute()
            .data
            or []
        )
        return dict(rows[0]) if rows and isinstance(rows[0], dict) else None
    except Exception as e:
        logger.error("get_whitelist_entry failed", exc=e)
        return None


def add_whitelist_entries(rows: list[dict]) -> dict:
    """Upsert whitelist rows via direct adapter (NOT the sync_whitelist RPC).

    The old RPC path (parity lib/services/storage/whitelist.ts) was the
    root cause of migration 005's duplicate whitelist rows — it didn't
    honor the composite PK (title_key+source) cleanly. We now upsert
    validated WhitelistRow models directly (on_conflict=title_key,source)
    so a re-add is idempotent, never a dup.
    """
    if not rows:
        return {"status": "ok", "whitelist": []}
    
    for _r in rows:
        _src = (_r.get("source") or "").lower()
        _tk = str(_r.get("title_key") or "").strip()
        
        if is_uuid_title_key(_tk):
            _title = str(_r.get("title") or "").strip()
            _nw = slugify_title_key(_title) if _title else ""
            if _nw and not is_uuid_title_key(_nw):
                _r["title_key"] = _nw
            else:
                _su = str(_r.get("series_url") or _r.get("url") or "").strip()
                if _su and "/series/" in _su:
                    _slug2 = _su.rstrip("/").split("/")[-1].split("?")[0]
                    _slug2 = slugify_title_key(_slug2)
                    if _slug2 and not is_uuid_title_key(_slug2):
                        _r["title_key"] = _slug2
    try:
        models = [WhitelistRow.model_validate(r) for r in rows]
        payload = [m.to_db() for m in models]
        get_supabase().table("whitelist").upsert(
            payload, on_conflict="title_key,source"
        ).execute()
        # Invalidate cache so next load picks up the new rows.
        load_whitelist.invalidate()
        from modules.manhwa.storage import recent_chapters as _rc
        _rc.invalidate_whitelist_origin_cache()
        return {"status": "ok", "whitelist": payload}
    except Exception as e:
        msg = str(e)
        logger.error("add_whitelist_entries failed", exc=e)
        return {"status": "error", "whitelist": [], "error": "database error"}

def auto_cleanup_stale_whitelist(days: int = 30, dry_run: bool = False) -> dict:
    """Remove whitelist entries that were added >`days` ago AND have NEVER
    been notified.

    IMPORTANT: the "never notified" signal MUST come from a column that is
    never pruned. It used to be `NOT EXISTS (dispatch_history ...)`, but
    dispatch_history is pruned to 3 days, so any series that last released
    more than 3 days ago looked "never notified" and got deleted — the
    biggest subscription leak. `whitelist.latest_sent_chapter` is written on
    every successful send and is never pruned, so it is the correct signal.

    A whitelist entry is a user subscription; it persists until the user
    removes it. This cleanup is therefore disabled by default (the retention
    worker only calls it when WHITELIST_AUTO_CLEANUP_DAYS > 0) and, when
    enabled, only drops entries that have never produced a single
    notification AND are older than `days`.
    """
    if days <= 0:
        return {"status": "ok", "removed": 0, "total": 0, "disabled": True}
    try:
        from datetime import datetime, timedelta, timezone
        from modules.manhwa.db import q
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Stale = added before cutoff AND never notified (latest_sent_chapter
        # is NULL/0). Never use recent_chapters/dispatch_history here — both
        # are pruned and would delete live subscriptions.
        sql = """
            SELECT w.title_key, w.source, w.title
            FROM whitelist w
            WHERE w.created_at < %s
              AND COALESCE(w.latest_sent_chapter, 0) = 0
        """
        stale = q(sql, [cutoff])
        if not stale:
            return {"status": "ok", "removed": 0, "total": 0}

        if not dry_run and stale:
            # L3 FIX: Use VALUES clause instead of unnest for composite key DELETE
            # unnest on two arrays produces element-wise rows which may not match correctly
            from modules.manhwa.db_adapter import get_conn, put_conn
            conn = None
            cur = None
            try:
                conn = get_conn()
                cur = conn.cursor()
                values_clause = ", ".join(["(%s, %s)"] * len(stale))
                params = []
                for r in stale:
                    params.extend([r["title_key"], r["source"]])
                cur.execute(
                    f"DELETE FROM whitelist WHERE (title_key, source) IN ({values_clause})",
                    params
                )
                # autocommit=True from get_conn() — no explicit commit needed
                load_whitelist.invalidate()
            finally:
                if cur is not None:
                    try:
                        cur.close()
                    except Exception:
                        pass
                if conn is not None:
                    try:
                        put_conn(conn)
                    except Exception:
                        pass

        return {"status": "ok", "removed": len(stale) if not dry_run else 0, "stale": len(stale), "dry_run": dry_run}
    except Exception as e:
        logger.error("auto_cleanup_stale_whitelist failed", exc=e)
        return {"status": "error", "error": str(e)[:200]}
