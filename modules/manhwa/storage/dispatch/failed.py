"""Failed-dispatches writes — record_failed upsert."""
from __future__ import annotations

from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger

logger = get_logger("storage:dispatch")


def record_failed(
    chapter_url: str,
    title_key: str = "",
    source: str = "",
    chapter_title: str = "",
    chapter_number: float | None = None,
    error_message: str = "",
    error_code: str = "UNKNOWN",
) -> None:
    """Upsert a row in failed_dispatches keyed by chapter_url."""
    if not chapter_url:
        return
    from datetime import datetime, timezone
    try:
        sb = get_supabase()
        # Fetch existing retry_count so we increment, not reset, on re-fail.
        existing = sb.table("failed_dispatches").select("retry_count").eq("chapter_url", chapter_url).limit(1).execute()
        prev = 0
        if existing.data:
            try:
                prev = int(existing.data[0].get("retry_count") or 0)
            except (TypeError, ValueError):
                prev = 0
        row = {
            "chapter_url": chapter_url,
            "title_key": title_key or "unknown",
            "source": source or "",
            "chapter_title": str(chapter_title or ""),
            "chapter_number": chapter_number,
            "error_message": str(error_message)[:500],
            "error_code": error_code,
            "status": "failed",
            "retry_count": prev + 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Upsert on chapter_url — atomic, no race condition
        sb.table("failed_dispatches").upsert(
            row, on_conflict="chapter_url"
        ).execute()
    except Exception as e:
        logger.error("record_failed failed", exc=e)
