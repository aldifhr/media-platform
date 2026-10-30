"""Window — retention for recent_chapters / dispatch_history (24h)."""
from modules.manhwa.core.time import RECENT_WINDOW_HOURS, cutoff_iso
from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger

logger = get_logger("storage:recent-chapters:window")


def prune_older_than(hours: int = RECENT_WINDOW_HOURS) -> int:
    cutoff = cutoff_iso(hours)
    try:
        from modules.manhwa.db import q as _q
        _dropped = _q("SELECT prune_recent_partition(%s::timestamptz)", [cutoff])
        if _dropped and _dropped[0].get("prune_recent_partition", 0) > 0:
            n = int(_dropped[0]["prune_recent_partition"])
            logger.info("pruned recent_chapters via DROP PARTITION", hours=hours, dropped=n)
            return n
    except Exception as _e:
        logger.debug("prune_recent_partition failed — falling back to DELETE", err=str(_e)[:160])
    try:
        sb = get_supabase()
        res = sb.table("recent_chapters").delete().lt("updated_time", cutoff).execute()
        n = len(res.data or [])
        if n:
            logger.info("pruned recent_chapters older than window", hours=hours, deleted=n)
        return n
    except Exception as e:
        logger.error("prune_older_than failed", exc=e)
        return 0


def prune_dispatch_history_older_than(hours: int = RECENT_WINDOW_HOURS) -> int:
    cutoff = cutoff_iso(hours)
    try:
        sb = get_supabase()
        res = sb.table("dispatch_history").delete().lt("sent_at", cutoff).execute()
        n = len(res.data or [])
        if n:
            logger.info("pruned dispatch_history older than window", hours=hours, deleted=n)
        return n
    except Exception as e:
        logger.error("prune_dispatch_history_older_than failed", exc=e)
        return 0
