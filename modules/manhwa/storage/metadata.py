"""Manga metadata storage — reads from whitelist (single source of truth)."""
from typing import Optional
from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger

logger = get_logger("storage:metadata")


def batch_get_manga_metadata(title_keys: list[str]) -> list[Optional[dict]]:
    """Get metadata from series_meta (canonical, whitelist minimal since 052)."""
    if not title_keys:
        return []
    try:
        res = (
            get_supabase()
            .table("series_meta")
            .select("title_key, cover, rating, genres, description, origin, type")
            .in_("title_key", title_keys)
            .execute()
        )
        rows = res.data or []
        by_key = {r["title_key"]: r for r in rows}
        return [by_key.get(tk) for tk in title_keys]
    except Exception as e:
        logger.error("batchGetMangaMetadata failed", exc=e)
        return []
