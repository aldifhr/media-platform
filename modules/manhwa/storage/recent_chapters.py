"""Recent chapters storage — orchestration shell.

Pure SQL helpers (prune, batch insert, fetch, dedup) live in
recent_chapters_sql.py. This module re-exports them for backward
compatibility and owns the claim_recent_chapters_for_dispatch orchestrator.

Mutable module state (_wl_origins, _WL_ORIGIN_TS) is NOT re-exported —
it lives in the sql module and is mutated there via `global`. Tests that
poke it import recent_chapters_sql directly.
"""
from datetime import datetime, timezone, timedelta

from modules.manhwa.db import get_conn, put_conn
from modules.manhwa.logger import get_logger
from modules.manhwa.core.fcfs import fcfs_key_for_item
from modules.manhwa.core.time import cutoff_iso
from modules.manhwa.storage.recent_chapters_caches import (  # noqa: F401
    invalidate_whitelist_origin_cache,
)
from modules.manhwa.storage.recent_chapters_ingest import (  # noqa: F401
    batch_insert_recent_chapters,
)
from modules.manhwa.storage.recent_chapters_sql import *  # noqa: F401,F403 — re-export public SQL helpers
from modules.manhwa.storage.recent_chapters_sql import (
    _row_to_item,
)
from modules.manhwa.storage.recent_chapters_window import (  # noqa: F401
    prune_dispatch_history_older_than,
    prune_older_than,
)

logger = get_logger("storage:recent_chapters")


def claim_recent_chapters_for_dispatch(
    whitelist: list[dict] | None = None, hours: int = 24, limit: int = 500
) -> list[dict]:
    """Deep queue claim — SELECT ... FOR UPDATE SKIP LOCKED + FCFS dedup + claim.

    Single seam: storage owns the SQL; services/claim.py re-exports this.
    """
    now = datetime.now(timezone.utc).isoformat()

    # DISABLED_SOURCES has to be honoured here, not only in collect(). Collect
    # stops adding new rows for a disabled source, but the rows it already wrote
    # stay in recent_chapters for the whole 24h window and would still be
    # claimed and notified, so turning a source off only stopped the new stuff.
    from modules.manhwa.config import disabled_sources as _disabled_sources

    _disabled: set[str] = _disabled_sources()

    _latest_sent: dict[tuple[str, str], float] = {}
    for w in whitelist or []:
        from modules.manhwa.core.text import slugify_title_key as _ntk

        tk = _ntk(str(w.get("title_key") or ""))
        src = str(w.get("source") or "")
        if src and src in _disabled:
            continue
        try:
            _ls = float(w.get("latest_sent_chapter") or 0)
        except (ValueError, TypeError):
            _ls = 0
        if tk and _ls:
            _latest_sent[(tk, src)] = max(_latest_sent.get((tk, src), 0), _ls)

    cutoff = cutoff_iso(hours)
    conn = None
    try:
        conn = get_conn()
        # BUG-6: autocommit=True (set in db_adapter.get_conn) releases the
        # FOR UPDATE SKIP LOCKED row lock immediately after SELECT, so two
        # concurrent workers can claim the same chapter → duplicate Discord.
        # Disable autocommit so the lock holds until conn.commit().
        conn.autocommit = False
        cur = conn.cursor()
        # Pushed-down claim SELECT.
        #
        # SCALABILITY FIX: the old query was `SELECT * FROM recent_chapters
        # WHERE release_date >= %s ORDER BY id DESC LIMIT 500` and did ALL
        # filtering in Python. That made the 500-row window the effective cap
        # on what could EVER be dispatched: once the newest 500 rows were all
        # already notified, the claim returned 0 no matter how many older
        # undispatched chapters existed. Dispatch silently degraded to
        # sent:0 (the deep-queue path was bypassed entirely via the
        # get_recent_chapters fallback).
        #
        # Now the cheap, exactly-indexable predicates run in SQL:
        #   - LEFT JOIN whitelist on (title_key, source), so both RAW and
        #     whitelist chapters can be claimed; whitelist metadata supplies
        #     the ceiling only for subscribed series.
        #   - NOT EXISTS dispatch_history on chapter_url [dispatch_history_pkey]
        #   - NOT EXISTS dispatch_claims on chapter_url [dispatch_claims_chapter_url_key]
        #   - ceiling vs whitelist.latest_sent_chapter
        #   - placeholder / missing-metadata row filters
        # The Python guards below stay as a second line of defence (they also
        # cover the normalized-title_key and legacy-pair paths that SQL cannot
        # express cheaply).
        cur.execute(
            """
            SELECT rc.* FROM recent_chapters rc
            LEFT JOIN whitelist w
              ON w.source = rc.source
             AND (w.title_key = rc.title_key
                  OR replace(normalize_title_key(w.title_key), ' ', '-')
                     = replace(normalize_title_key(rc.title_key), ' ', '-'))
            WHERE rc.release_date >= %s
              AND NOT EXISTS (
                    SELECT 1 FROM dispatch_history dh
                    WHERE dh.chapter_url = rc.chapter_url
              )
              AND NOT EXISTS (
                    SELECT 1 FROM dispatch_claims dc
                    WHERE dc.chapter_url = rc.chapter_url
                      AND dc.expires_at >= %s
              )
              -- Ceiling. Mirrors the Python guard exactly: a chapter_num of
              -- 0/NULL is "unknown", and Python skips the ceiling for those
              -- (`if _cn:` is false), so SQL must NOT drop them here.
              AND (COALESCE(rc.chapter_num, 0) = 0
                   OR COALESCE(w.latest_sent_chapter, 0) = 0
                   OR rc.chapter_num > w.latest_sent_chapter)
              AND rc.chapter_url NOT LIKE 'https://x/%%'
              AND rc.chapter_url NOT LIKE 'http://x/%%'
              AND length(COALESCE(rc.series_url, '')) >= 10
              AND (COALESCE(rc.origin, '') <> '' OR COALESCE(rc.cover, '') <> '')
            ORDER BY rc.id DESC
            LIMIT %s
            FOR UPDATE OF rc SKIP LOCKED
            """,
            (cutoff, now, limit),
        )
        rows = cur.fetchall()
        candidates: list[dict] = []
        for r in rows:
            tk = str(r.get("title_key") or "")
            src = str(r.get("source") or "")
            from modules.manhwa.core.text import slugify_title_key as _ntk2

            ntk = _ntk2(tk)
            if src in _disabled:
                continue
            _cu = str(r.get("chapter_url") or "")
            _su = str(r.get("series_url") or "")
            _orig = str(r.get("origin") or "")
            _cov = r.get("cover")
            if _cu.startswith("https://x/") or _cu.startswith("http://x/"):
                continue
            if len(_su) < 10:
                continue
            if not _orig and not _cov:
                continue
            candidates.append(dict(r))

        if not candidates:
            conn.commit()
            return []

        urls = [c.get("chapter_url") for c in candidates if c.get("chapter_url")]
        fcfs_keys = [fcfs_key_for_item(c) for c in candidates]
        already_urls: set[str] = set()
        already_fcfs: set[str] = set()
        if urls:
            ph = ",".join(["%s"] * len(urls))
            cur.execute(f"SELECT chapter_url FROM dispatch_history WHERE chapter_url IN ({ph})", urls)
            already_urls |= {row["chapter_url"] for row in cur.fetchall()}  # type: ignore
        if fcfs_keys:
            uniq_fk = list(set(fcfs_keys))
            ph2 = ",".join(["%s"] * len(uniq_fk))
            cur.execute(f"SELECT fcfs_key FROM dispatch_history WHERE fcfs_key IN ({ph2})", uniq_fk)
            already_fcfs |= {row["fcfs_key"] for row in cur.fetchall() if row.get("fcfs_key")}  # type: ignore
            ph3 = ",".join(["%s"] * len(uniq_fk))
            cur.execute(f"SELECT fcfs_key FROM dispatch_claims WHERE fcfs_key IN ({ph3}) AND expires_at >= %s", uniq_fk + [now])
            already_fcfs |= {row["fcfs_key"] for row in cur.fetchall() if row.get("fcfs_key")}  # type: ignore
        _legacy_pairs: set[tuple[str, str]] = set()
        try:
            _tk_list = list({str(c.get("title_key") or "") for c in candidates if c.get("title_key")})
            if _tk_list:
                ph_tk = ",".join(["%s"] * len(_tk_list))
                cur.execute(f"SELECT title_key, chapter_title FROM dispatch_history WHERE title_key IN ({ph_tk})", _tk_list)
                for _r in cur.fetchall():
                    _tkh = str(_r.get("title_key") or "")
                    _cth = str(_r.get("chapter_title") or "")
                    if _tkh and _cth:
                        _legacy_pairs.add((_tkh, _cth))
                        try:
                            _cnorm = ("%.10g" % float(_cth))
                            _legacy_pairs.add((_tkh, _cnorm))
                        except (ValueError, TypeError):
                            pass
        except Exception:
            _legacy_pairs = set()

        to_claim: list[dict] = []
        for c in candidates:
            u = c.get("chapter_url")
            fk = fcfs_key_for_item(c)
            if u in already_urls or fk in already_fcfs:
                continue
            _tk_c = str(c.get("title_key") or "")
            _ch_c = str(c.get("chapter") or c.get("chapter_num") or "")
            try:
                _ch_norm = ("%.10g" % float(_ch_c)) if _ch_c else _ch_c
            except (ValueError, TypeError):
                _ch_norm = _ch_c
            if _tk_c and _ch_c and ((_tk_c, _ch_c) in _legacy_pairs or (_tk_c, _ch_norm) in _legacy_pairs):
                continue
            try:
                _cn = float(str(c.get("chapter") or c.get("chapter_num") or 0) or 0)
            except (ValueError, TypeError):
                _cn = 0
            if _cn:
                _tk2 = str(c.get("title_key") or "")
                from modules.manhwa.core.text import slugify_title_key as _ntk3

                _ntk_c = _ntk3(_tk2)
                _src2 = str(c.get("source") or "")
                _ceil = _latest_sent.get((_ntk_c, _src2), 0) or _latest_sent.get((_tk2, _src2), 0)
                if _ceil and _cn <= _ceil:
                    continue
            to_claim.append(c)

        _inserted_fks: set[str] = set()
        if to_claim:
            try:
                _seen_fcfs: set[str] = set()
                _claim_rows: list[tuple] = []
                _claim_expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
                for c in to_claim:
                    _fk = fcfs_key_for_item(c)
                    if _fk in _seen_fcfs:
                        continue
                    _seen_fcfs.add(_fk)
                    _claim_rows.append(
                        (
                            c.get("title_key"),
                            c.get("chapter_url"),
                            _fk,
                            datetime.now(timezone.utc).isoformat(),
                            _claim_expires,
                            "pending",
                        )
                    )
                if not _claim_rows:
                    conn.commit()
                    return [_row_to_item(r) for r in to_claim]
                _claim_ph = ",".join(["(%s,%s,%s,%s,%s,%s)"] * len(_claim_rows))
                _claim_vals: list = []
                for _r in _claim_rows:
                    _claim_vals.extend(_r)
                cur.execute(
                    # The unique index on fcfs_key has no expiry, so a stale
                    # (expired) claim would make DO NOTHING swallow the re-claim
                    # and the chapter could never be sent. Refresh the row when
                    # the existing one has already expired; keep DO NOTHING while
                    # another worker's claim is still live.
                    f"INSERT INTO dispatch_claims (title_key, chapter_url, fcfs_key, created_at, expires_at, status) "
                    f"VALUES {_claim_ph} "
                    f"ON CONFLICT (fcfs_key) DO UPDATE SET "
                    f"chapter_url = EXCLUDED.chapter_url, "
                    f"created_at = EXCLUDED.created_at, "
                    f"expires_at = EXCLUDED.expires_at, "
                    f"status = EXCLUDED.status "
                    f"WHERE dispatch_claims.expires_at < EXCLUDED.created_at "
                    f"RETURNING fcfs_key",
                    _claim_vals,
                )
                _inserted_fks = {row["fcfs_key"] for row in cur.fetchall() if row.get("fcfs_key")}
            except Exception as e:
                logger.warn("dispatch_claims insert failed", err=str(e)[:120])
                try:
                    conn.rollback()
                except Exception as e:
                    logger.warn("claim: rollback failed", err=str(e)[:120])
                conn.commit()
                return []

        # P1 fix: only return items whose INSERT actually succeeded (won the ON CONFLICT race)
        if to_claim and _inserted_fks:
            _claimed_items = [c for c in to_claim if fcfs_key_for_item(c) in _inserted_fks]
        else:
            _claimed_items = []
        conn.commit()
        return [_row_to_item(r) for r in _claimed_items]
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception as e:
                logger.warn("claim: rollback failed", err=str(e)[:120])
        # P2 fix: fail-closed — on any error, return [] (never fall back to
        # get_recent_chapters which would bypass the claim guard and send
        # unclaimed chapters).
        logger.error("claim_recent_chapters_for_dispatch failed — fail-closed returning []", exc=e)
        return []
    finally:
        if conn:
            try:
                put_conn(conn)
            except Exception as e:
                logger.warn("claim: put_conn failed", err=str(e)[:120])
