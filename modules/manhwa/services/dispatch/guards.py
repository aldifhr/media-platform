"""Dispatch guards: chapter ceilings and implausible chapter numbers.

Extracted from app/cron/dispatch_mod.py. dispatch_mod re-exports these symbols
for backward compatibility with existing call sites and tests.
"""

from __future__ import annotations


def load_chapter_ceilings(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], float]:
    """Max already-dispatched chapter per (title_key, source), cross-source.

    Cross-source on purpose: the ceiling is "how far has this series already
    been notified", and a series is one series regardless of which site it was
    read from. Scoping it per source meant a brand new source started at zero,
    so when one re-uploaded old chapters with a fresh release_date (ch 23/24/25
    of a series already shipped through ch330 on shinigami) all of them passed
    the ceiling and were notified.

    Extracted from dispatch() so the loader is testable. It was not, and that
    is exactly how `_row[0]` on a RealDictRow survived: the KeyError was caught
    by a bare `except: pass`, so _ceilings stayed empty and this guard blocked
    nothing for as long as it existed.

    Returns {} on any DB problem — a missing ceiling must never stop dispatch,
    because FCFS still covers the already-notified case.
    """
    out: dict[tuple[str, str], float] = {}
    if not pairs:
        return out
    try:
        from modules.manhwa.db import get_conn, put_conn

        cc = get_conn()
        try:
            cur = cc.cursor()
            for tk, src in pairs:
                # NULLIF discards non-numeric labels; max() then ignores them,
                # matching the float() guard in the sanity check.
                cur.execute(
                    "SELECT max(NULLIF(trim(chapter_title), '')::numeric) AS max_ch "
                    "FROM dispatch_history WHERE title_key = %s",
                    (tk,),
                )
                row = cur.fetchone()
                # The pool uses RealDictCursor, so a row is a DICT. Read by
                # column name; isinstance keeps a tuple cursor working too.
                if row is None:
                    val = None
                elif isinstance(row, dict):
                    val = row.get("max_ch")
                else:
                    val = row[0]
                if val is not None:
                    out[(tk, src)] = float(val)
        finally:
            # Return the borrowed connection — get_conn() holds a semaphore
            # slot; dropping it without put_conn starves the pool.
            try:
                put_conn(cc)
            except Exception:
                pass
    except Exception:
        pass
    return out


def is_implausible_chapter(chapter: object, ceiling: float | None, batch_count: int) -> bool:
    """True when a chapter number is a bad label rather than a release.

    Observed live: shinigami served chapter_number '26596' for a series whose
    real chapters are 1..266 (its own chapter list shows 26596 sitting beside
    265), and it was dispatched — spamming the channel AND poisoning the
    ceiling to 26596, which then blocks every real chapter after it.

    Two ways out, so a genuine renumbering is not silently lost:
      * <= 2000 -> accept. Covers a normal jump and, more usefully, rejects a
        date-as-number slip like 20261008.
      * > 2000 and arriving as a ~3-chapter burst within 50 of the ceiling ->
        accept, because a renumber legitimately emits 4000/4001/4002 together.
    """
    if ceiling is None:
        return False
    try:
        n = float(str(chapter or 0))
    except (ValueError, TypeError):
        return False
    if n <= 2000:
        return False
    is_renumber = (
        batch_count == 3 and 0 < (n - ceiling) <= 50 and str(chapter or "").strip().isdigit()
    )
    return not is_renumber
