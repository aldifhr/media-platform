"""Dispatch claims — claim/unclaim lifecycle and the atomic claim guard.

Three TTLs live in this module and they are NOT the same thing, so they are
named rather than inlined:

* ``CLAIM_TTL_S``      — the in-run claim written by ``claim_and_record``. It
  exists only to stop two concurrent runs sending the same chapter, so it is
  short: a chapter that genuinely fails to send must become eligible again
  within the hour instead of being suppressed for a day.
* ``ONBOARDING_TTL_S`` — ``mark_claimed``, used when a NEW whitelist entry is
  added so the chapters that already exist are not re-notified. Here a long
  TTL is the point.
* ``BACKFILL_TTL_S``   — ``mark_history_only``, the silent "already seen"
  marker for HTML-scraped backlog. Effectively permanent.

``dispatch_claims`` is the TRANSIENT table (TTL-bounded, reaped by
``unclaim_stale``). ``dispatch_history`` is the PERMANENT record of a message
that actually reached a human. Nothing in this module may delete from
``dispatch_history`` — see ``unclaim``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from modules.manhwa.db import get_supabase
from modules.manhwa.logger import get_logger

logger = get_logger("storage:dispatch")

# In-run claim: short, so a failed send is retried within the hour.
CLAIM_TTL_S = 3600
# New-whitelist-entry suppression: long by design.
ONBOARDING_TTL_S = 24 * 3600
# Silent backlog marker: "never notify this again".
BACKFILL_TTL_S = 365 * 24 * 3600


def mark_claimed(urls: list[str], title_keys: list[str], expires_hours: int | None = None) -> None:
    """Mark chapter URLs as claimed in dispatch_claims (with TTL).
    Used when adding a NEW whitelist entry: existing chapters shouldn't be
    re-notified. They auto-expire after `expires_hours`."""
    if not urls:
        return
    if expires_hours is None:
        expires_hours = ONBOARDING_TTL_S // 3600
    expires = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).isoformat()
    rows = [
        {"chapter_url": u, "title_key": tk or "unknown", "expires_at": expires}
        for u, tk in zip(urls, title_keys)
        if u
    ]
    if not rows:
        return
    try:
        # upsert on conflict chapter_url to refresh expiry
        get_supabase().table("dispatch_claims").upsert(
            rows, on_conflict="chapter_url"
        ).execute()
    except Exception as e:
        if "does not exist" in str(e):
            return
        logger.error("mark_claimed failed", exc=e)


def unclaim(chapter_url: str) -> None:
    """Release the in-run claim for ``chapter_url`` so it can be retried.

    This touches ``dispatch_claims`` ONLY. It used to also DELETE the
    ``dispatch_history`` row, which was the single worst duplicate-notification
    leak in the system: ``senders._send_item`` calls ``unclaim`` on every
    transient Discord failure (a ``None`` return, or any exception), so one
    hiccup while re-scraping a chapter that had already been delivered erased
    the only record that it was delivered. The next run then saw no history
    row, no live claim, and re-posted a chapter that was already in the
    channel.

    ``dispatch_history`` means "a human received this". A failed *retry* is
    not evidence to the contrary, so it must never be deleted here. The
    permanent record is written by ``complete_dispatch_claim`` and removed by
    nothing — retention owns that table.
    """
    if not chapter_url:
        return
    try:
        get_supabase().table("dispatch_claims").delete().eq("chapter_url", chapter_url).execute()
    except Exception as e:
        if "does not exist" in str(e):
            return
        logger.error("unclaim dispatch_claims failed", exc=e)


def unclaim_stale(cutoff_iso: str) -> int:
    """Delete dispatch_claims rows that are no longer held by any live run.

    The cutoff is applied to `expires_at`, NOT `created_at`.

    `created_at` used to be the filter and it matched nothing: migration 057
    recreated dispatch_claims without a DEFAULT on created_at, so the column was
    NULL on every row and `created_at < cutoff` was never true. Stuck claims
    therefore never expired — they held a URL until dispatch's guard rejected
    the item as already-claimed, which is how cron came to report sent:0 while
    nothing was delivered.

    Filtering on `expires_at` is also the semantically right choice: a claim is
    "stuck" when its TTL has run out, which is exactly expires_at < cutoff. It
    works whether or not created_at is populated.

    Returns count deleted.
    """
    if not cutoff_iso:
        return 0
    try:
        res = (
            get_supabase()
            .table("dispatch_claims")
            .delete()
            .lt("expires_at", cutoff_iso)
            .execute()
        )
        return len(res.data or [])
    except Exception as e:
        # 055 dropped dispatch_claims (transient) — table missing is expected after migration, not an error
        if "does not exist" in str(e) or "dispatch_claims" in str(e) and "not exist" in str(e).lower():
            logger.debug("unclaim_stale skip — dispatch_claims dropped (055)", exc=e)
            return 0
        logger.error("unclaim_stale failed", exc=e)
        return 0


def _release_claim_conn(conn, restore_autocommit: bool) -> None:
    """Return a borrowed connection to the pool with autocommit restored.

    ``claim_and_record`` takes the connection out of autocommit so its
    pre-check and its claim insert share one transaction. Every other caller
    in this codebase borrows autocommit=True connections and issues bare
    statements, so the flag has to go back before ``put_conn`` — otherwise the
    next borrower's first write silently opens a transaction that is never
    committed and the row vanishes.
    """
    if restore_autocommit:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.autocommit = True
        except Exception:
            pass
    try:
        from modules.manhwa.db import put_conn

        put_conn(conn)
    except Exception:
        pass


def claim_and_record(urls: list[str], title_keys: list[str], sources: list[str], instance_id: str, chapter_titles: list[str] | None = None, fcfs_keys: list[str] | None = None) -> list[bool]:
    """Atomic claim guard — returns which urls THIS run is allowed to send.

    IMPORTANT: this does NOT write to dispatch_history. It only records a
    short-TTL claim in dispatch_claims so concurrent runs don't double-send.
    The actual "notified" record (dispatch_history) is written by dispatch()
    ONLY for chapters that were successfully sent to Discord — keeping the
    user-facing 'Notified' view accurate (it shows what was actually sent,
    not every chapter the pipeline touched).

    Race-safety: pre-check dispatch_history (already notified) + dispatch_claims
    (claimed by a concurrent run, non-expired). If neither has the url (or
    fcfs_key, for URL-rotating sources), we claim it (upsert into
    dispatch_claims with a TTL) and return True. A url/fcfs already present in
    either returns False (skip).

    NOTE: the guard keys off BOTH chapter_url AND fcfs_key. Sources
    rotate chapter URLs every scrape, so URL-only dedupe misses cross-source /
    cross-run duplicates of the SAME title+chapter. fcfs_key = normalized
    title+chapter is stable across URL rotations, so we also skip when the
    fcfs_key was already notified (this is what actually prevents duplicate
    Discord notifications for the same release from two sources).
    """
    if not urls:
        return []
    now = datetime.now(timezone.utc)
    # Atomic pre-check in single transaction (one connection) to avoid race between check and claim.
    already: set[str] = set()
    fcfs_already: set[str] = set()
    _pre_conn = None
    _restore_autocommit = False
    try:
        from modules.manhwa.db import get_conn
        _pre_conn = get_conn()
        # The pool hands out autocommit=True connections (db/adapter.py), which
        # makes every statement above its own transaction and turns the
        # "atomic pre-check" below into three independent commits: read
        # history, read claims, write claims. Two concurrent runs could both
        # read "not claimed" and both write, and both would be told they own
        # the chapter. Take the connection out of autocommit for the duration
        # so the read and the write really do land in one transaction, and put
        # it back before the connection returns to the pool — every other
        # borrower in this codebase assumes autocommit.
        try:
            _pre_conn.autocommit = False
            _restore_autocommit = True
        except Exception:
            _restore_autocommit = False
        _cur = _pre_conn.cursor()
        if urls:
            ph = ",".join(["%s"] * len(urls))
            _cur.execute(f"SELECT chapter_url FROM dispatch_history WHERE chapter_url IN ({ph})", urls)
            already |= {r["chapter_url"] for r in _cur.fetchall()}
            try:
                _cur.execute(f"SELECT chapter_url FROM dispatch_claims WHERE chapter_url IN ({ph}) AND expires_at >= %s", urls + [now.isoformat()])
                already |= {r["chapter_url"] for r in _cur.fetchall()}
            except Exception as ce:
                if "does not exist" not in str(ce):
                    raise
        if fcfs_keys:
            uniq = list(set([k for k in fcfs_keys if k]))
            if uniq:
                ph2 = ",".join(["%s"] * len(uniq))
                _cur.execute(f"SELECT fcfs_key FROM dispatch_history WHERE fcfs_key IN ({ph2})", uniq)
                fcfs_already |= {r["fcfs_key"] for r in _cur.fetchall() if r.get("fcfs_key")}
                try:
                    # Expiry filter is mandatory: a claim that already expired
                    # is not held by anyone, and without this filter a stale row
                    # blocks re-claiming the chapter forever.
                    _cur.execute(
                        f"SELECT fcfs_key FROM dispatch_claims WHERE fcfs_key IN ({ph2}) AND expires_at >= %s",
                        uniq + [now.isoformat()],
                    )
                    fcfs_already |= {r["fcfs_key"] for r in _cur.fetchall() if r.get("fcfs_key")}
                except Exception as ce:
                    if "does not exist" not in str(ce):
                        raise
    except Exception as e:
        if "does not exist" not in str(e):
            logger.error("claim atomic precheck failed", exc=e)
        if _pre_conn:
            try:
                _pre_conn.rollback()
            except Exception:
                pass

    result: list[bool] = []
    new_claims: list[dict] = []
    claimed_fk: set[str] = set()  # fcfs_keys this batch already granted
    expires = (now + timedelta(seconds=CLAIM_TTL_S)).isoformat()
    for i, (u, tk, src) in enumerate(zip(urls, title_keys, sources)):
        if not u:
            result.append(False)
            continue
        fk = fcfs_keys[i] if fcfs_keys and i < len(fcfs_keys) else None
        if u in already or (fk and (fk in fcfs_already or fk in claimed_fk)):
            result.append(False)
            continue
        # claim it (short TTL so a genuinely-new chapter released later isn't
        # suppressed forever). Claim KEY = fcfs_key when available, so a
        # different source scraping the SAME title+chapter (different URL)
        # cannot claim it a second time -> no cross-source double-send.
        #
        # `created_at` is set EXPLICITLY. dispatch_claims was recreated by
        # migration 057 without a DEFAULT on that column, so omitting it stored
        # NULL. `unclaim_stale()` used to filter `created_at < cutoff`, which
        # then matched nothing: claims accumulated as live rows with no way to
        # be released, so dispatch's URL guard rejected every item and cron
        # reported sent:0 indefinitely. Observed live: 3 rows, 3 NULL
        # created_at, 0 rows matching the filter.
        #
        # unclaim_stale() now filters `expires_at < cutoff` (see its docstring),
        # so the column no longer gates the reaper — but it is still written
        # because it is the only audit trail for when a claim was taken, and
        # migration 078 added a DEFAULT for new rows.
        row = {
            "chapter_url": u,
            "title_key": tk or "unknown",
            "created_at": now.isoformat(),
            "expires_at": expires,
        }
        if fk:
            row["fcfs_key"] = fk
            claimed_fk.add(fk)
        new_claims.append(row)
        result.append(True)
    if new_claims:
        # Dedupe by fcfs_key
        seen_fk: set[str] = set()
        deduped = []
        for r in new_claims:
            k = r.get("fcfs_key")
            if k:
                if k in seen_fk:
                    continue
                seen_fk.add(k)
            deduped.append(r)
        with_fk = [r for r in deduped if r.get("fcfs_key")]
        without_fk = [r for r in deduped if not r.get("fcfs_key")]
        try:
            # Use the same _pre_conn for atomic claim insert (commit together with pre-check)
            if _pre_conn is not None:
                # _pre_conn already has transaction open from pre-check; do inserts then commit
                if with_fk:
                    # use INSERT ... ON CONFLICT via raw SQL to stay on same conn
                    for row in with_fk:
                        cols = list(row.keys())
                        vals = [row[c] for c in cols]
                        # Build INSERT ... ON CONFLICT (fcfs_key) DO UPDATE
                        # P1 fix: an unconditional DO UPDATE would let a second
                        # worker silently overwrite an ACTIVE claim. Guard the
                        # update so it only refreshes a claim that has already
                        # expired — a stale row otherwise blocks the re-claim
                        # forever (the unique index has no expiry).
                        placeholders = ", ".join(["%s"] * len(cols))
                        col_list = ", ".join(cols)
                        sql = (
                            f"INSERT INTO dispatch_claims ({col_list}) VALUES ({placeholders}) "
                            f"ON CONFLICT (fcfs_key) DO UPDATE SET "
                            f"chapter_url = EXCLUDED.chapter_url, "
                            f"expires_at = EXCLUDED.expires_at "
                            f"WHERE dispatch_claims.expires_at < %s"
                        )
                        _cur.execute(sql, vals + [now.isoformat()])
                if without_fk:
                    for row in without_fk:
                        cols = list(row.keys())
                        vals = [row[c] for c in cols]
                        placeholders = ", ".join(["%s"] * len(cols))
                        col_list = ", ".join(cols)
                        # P1 fix: DO NOTHING for consistency — first INSERT wins.
                        sql = f"INSERT INTO dispatch_claims ({col_list}) VALUES ({placeholders}) ON CONFLICT (chapter_url) DO NOTHING"
                        _cur.execute(sql, vals)
                _pre_conn.commit()
            else:
                # Fallback to old path if pre_conn failed
                if with_fk:
                    get_supabase().table("dispatch_claims").upsert(with_fk, on_conflict="fcfs_key").execute()
                if without_fk:
                    get_supabase().table("dispatch_claims").upsert(without_fk, on_conflict="chapter_url").execute()
        except Exception as ins_err:
            if "does not exist" not in str(ins_err):
                logger.error("dispatch_claims upsert failed", exc=ins_err)
            if _pre_conn is not None:
                try:
                    _pre_conn.rollback()
                except Exception:
                    pass
        finally:
            if _pre_conn is not None:
                _release_claim_conn(_pre_conn, _restore_autocommit)
                _pre_conn = None
    else:
        # No claims to insert, but need to close pre_conn transaction
        if _pre_conn is not None:
            try:
                _pre_conn.commit()
            except Exception:
                try:
                    _pre_conn.rollback()
                except Exception:
                    pass
            _release_claim_conn(_pre_conn, _restore_autocommit)
    return result


def complete_dispatch_claim(
    chapter_url: str, duplicate_url: str | None, instance_id: str, title_key: str = "", source: str = "", fcfs_key: str | None = None, chapter_title: str = "", cover: str = "", series_url: str = ""
) -> None:
    """Record sent chapter in dispatch_history (so next run skips it)
    AND remove the short-TTL claim from dispatch_claims so the
    queue-depth counter drops. Without this, dispatch_claims only
    grows (every claimed url stays forever) → Queue Depth shows
    stale "pending" forever even though the chapter was sent.

    `fcfs_key` (if provided) is persisted on the dispatch_history row so
    cross-run FCFS dedupe works: another source publishing the same
    title+chapter within 24h is skipped even after the dispatch_claims
    row (which this function deletes) is gone.

    `cover` / `series_url` (when available) are stored on the row so the
    dispatch-history UI can render the chapter card (cover + clickable
    series link) instead of an empty placeholder.
    """
    try:
        row = {
            "chapter_url": chapter_url,
            "title_key": title_key or "unknown",
            "source": source or None,
            "chapter_title": str(chapter_title or ""),
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        if cover:
            row["cover"] = cover
        if series_url:
            row["series_url"] = series_url
        if fcfs_key:
            row["fcfs_key"] = fcfs_key
        sb = get_supabase()
        # C2 FIX: Use upsert on_conflict=fcfs_key instead of DELETE+INSERT
        # to prevent duplicate rows when two concurrent runs target same fcfs_key
        # fallback to chapter_url upsert then plain insert
        try:
            if fcfs_key:
                sb.table("dispatch_history").upsert(
                    row, on_conflict="fcfs_key"
                ).execute()
            else:
                sb.table("dispatch_history").upsert(
                    row, on_conflict="chapter_url"
                ).execute()
        except Exception as _e:
            _err = str(_e)
            if "ON CONFLICT" in _err:
                # Unique index on fcfs_key may not exist yet (migration gap).
                # Fall back to chapter_url, which has a guaranteed unique index.
                try:
                    sb.table("dispatch_history").upsert(row, on_conflict="chapter_url").execute()
                except Exception as _e2:
                    logger.error("complete_dispatch_claim history failed (fallback)", exc=_e2, chapter_url=chapter_url[:60], fcfs_key=fcfs_key)
                    raise
            elif "dispatch_history_uq" in _err or ("duplicate" in _err.lower() and "dispatch_history" in _err.lower()):
                logger.info("complete_dispatch_claim duplicate skip (dispatch_history_uq)", chapter_url=chapter_url[:60], fcfs_key=fcfs_key)
            else:
                logger.error("complete_dispatch_claim history failed", exc=_e, chapter_url=chapter_url[:60], fcfs_key=fcfs_key)
                raise
    except Exception as e:
        # outer fallback already logged; dispatch_history_uq duplicate is not an error (already-sent)
        if "dispatch_history_uq" in str(e) or ("duplicate" in str(e).lower() and "dispatch_history" in str(e).lower()):
            pass
        elif "ON CONFLICT" not in str(e):
            logger.error("complete_dispatch_claim history failed", exc=e, chapter_url=chapter_url[:60], fcfs_key=fcfs_key)
    # Drop the claim so queue-depth reflects reality.
    try:
        get_supabase().table("dispatch_claims").delete().eq(
            "chapter_url", chapter_url
        ).execute()
    except Exception as e:
        logger.error("complete_dispatch_claim unclaim failed", exc=e)

