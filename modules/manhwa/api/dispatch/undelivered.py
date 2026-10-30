"""Chapters that are eligible but were never dispatched.

`/dispatch/failed-dispatches` lists chapters whose Discord send FAILED and
are waiting on the retry queue. This lists a different set: chapters that
were never attempted at all.

Two reasons a chapter lands here:

  - it aged out of the 24h dispatch window (the collector stops offering it),
  - the dispatch ceiling or the implausible-chapter guard rejected it before
    the send phase ever saw it.

Either way the reader is subscribed to that (title_key, source) and simply
never got told. There is no other surface that shows this: the ledger only
records what WAS sent, so absence is invisible by construction.

POST /dispatch/undelivered/resend re-runs the real dispatch path for the
selected chapters — it does not hand-craft an embed. That way the ceiling
guard, the FCFS dedupe, the guild filters and the rate limits all still
apply, and a chapter that genuinely must not be sent is still refused.
"""
from __future__ import annotations

import time
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.manhwa.core.sql_fragments import RC_WL_JOIN_ON
from modules.manhwa.core.text import deslugify_title_key
from modules.manhwa.logger import get_logger
from modules.manhwa.utils.request_auth import require_monitor_auth
from modules.manhwa.utils.responses import err, ok, unauthorized

logger = get_logger("api:undelivered")
router = APIRouter()

# Ceiling rejects chapters the dispatcher will never send. Reporting them as
# "send now" candidates would page the operator on data that cannot move, so
# they are counted separately and reported, not offered as resendable.
_MAX_RESEND_LIMIT = 200

# Must match _RECENT_CHAPTERS_RETENTION_DAYS in app/tasks/retention.py. A
# chapter older than this is pruned and can never be listed or resent.
_MAX_WINDOW_H = 24 * 7


@router.get("/undelivered")
async def undelivered(request: Request, hours: int = 168, limit: int = 100):
    """Whitelisted chapters with no dispatch_history entry, newest first.

    `hours` bounds how far back to look; the default is 7 days, which is
    wider than the 24h dispatch window on purpose — the point is to surface
    what the window dropped.

    Capped at the recent_chapters retention window (7 days). Past that the
    rows are pruned and there is nothing left to list, so the response says
    so rather than returning a short list that reads as complete.
    """
    if not require_monitor_auth(request):
        return unauthorized()

    # Cap at the retention window. Offering 30 days when recent_chapters is
    # pruned at 7 makes the 30-day tab silently return the same rows as 7 days,
    # which reads as "we checked and there was nothing" when the truth is the
    # data is gone.
    try:
        hours = max(1, min(int(hours), _MAX_WINDOW_H))
        limit = max(1, min(int(limit), _MAX_RESEND_LIMIT))
    except (TypeError, ValueError):
        return err("invalid params", status_code=400)

    started = time.monotonic()
    try:
        from modules.manhwa.db import get_conn, put_conn
        from modules.manhwa.services.fcfs import fcfs_key_for_item

        conn = get_conn()
        try:
            cur = conn.cursor()  # RealDictCursor: rows come back as dicts
            cur.execute(
                f"""
                SELECT rc.title, rc.title_key, rc.chapter, rc.source,
                       rc.chapter_url, rc.series_url, rc.cover,
                       rc.release_date, rc.type, rc.origin
                FROM recent_chapters rc
                JOIN whitelist w
                  ON {RC_WL_JOIN_ON}
                WHERE rc.release_date > now() - make_interval(hours => %s)
                  AND NOT EXISTS (
                        SELECT 1 FROM dispatch_history dh
                        WHERE dh.title_key = rc.title_key
                          AND dh.chapter_title = rc.chapter
                      )
                ORDER BY rc.release_date DESC
                LIMIT %s
                """,
                (hours, limit),
            )
            # get_conn() uses a RealDictCursor pool, so rows are dicts.
            rows: list[dict] = list(cur.fetchall() or [])

            # fcfs_key is the cross-source identity. A chapter already sent
            # from another source is NOT undelivered even though this exact
            # (title_key, source) row has no ledger entry — the reader got
            # the chapter, just from the other feed. Counting it would send a
            # duplicate the moment the user pressed resend.
            cur.execute("SELECT fcfs_key FROM dispatch_history WHERE fcfs_key IS NOT NULL")
            dh_rows: list[dict] = list(cur.fetchall() or [])
            sent = {str(r["fcfs_key"]) for r in dh_rows if r.get("fcfs_key")}
        finally:
            put_conn(conn)
    except Exception as e:
        logger.warn("undelivered: query failed", err=str(e)[:160])
        return err("query failed", status_code=500)

    items: list[dict] = []
    skipped_already_sent = 0
    for r in rows:
        tk = str(r.get("title_key") or "")
        ch = r.get("chapter")
        if sent and fcfs_key_for_item({"title_key": tk, "chapter": ch}) in sent:
            skipped_already_sent += 1
            continue
        rel = r.get("release_date")
        items.append(
            {
                "title": r.get("title") or tk,
                "titleKey": tk,
                "chapter": str(ch or ""),
                "source": r.get("source") or "",
                "chapterUrl": r.get("chapter_url") or "",
                "seriesUrl": r.get("series_url") or "",
                "cover": r.get("cover") or "",
                "releaseDate": rel.isoformat() if isinstance(rel, datetime) else str(rel or ""),
                "type": r.get("type") or "",
                "origin": r.get("origin") or "",
            }
        )

    return JSONResponse(
        content={
            "success": True,
            "data": {
                "items": items,
                "count": len(items),
                "skippedAlreadySent": skipped_already_sent,
                "hours": hours,
                # True when the caller asked for a window wider than the data
                # survives. The FE must not present this as "nothing missed".
                "truncatedAtRetention": hours >= _MAX_WINDOW_H,
                "maxWindowHours": _MAX_WINDOW_H,
            },
        }
    )


@router.post("/undelivered/resend")
async def resend_undelivered(request: Request):
    """Re-run the normal dispatch path for the given chapter_urls."""
    if not require_monitor_auth(request):
        return unauthorized()

    try:
        body = await request.json()
    except Exception:
        return err("invalid json", status_code=400)

    urls = body.get("chapterUrls") or []
    if not isinstance(urls, list) or not urls:
        return err("chapterUrls required", status_code=400)
    urls = [str(u) for u in urls if u][:_MAX_RESEND_LIMIT]

    try:
        from modules.manhwa.db import get_supabase

        # Load the full rows: dispatch() needs title, chapter, title_key,
        # source, url and the embed fields. Reconstructing them from the
        # request would let a caller inject an arbitrary embed.
        rows = (
            get_supabase()
            .table("recent_chapters")
            # column list read off information_schema: recent_chapters has
            # chapter_url but no `url`, and has both `chapter` and
            # `chapter_num`. Selecting a column that does not exist raised
            # UndefinedColumn, which the except below turned into a bare
            # "lookup failed" with no clue why.
            .select("title,title_key,chapter,source,chapter_url,series_url,cover,description,genres,rating,type,origin,release_date,updated_time")
            .in_("chapter_url", urls)
            .execute()
            .data
            or []
        )
    except Exception as e:
        logger.warn("undelivered: resend load failed", err=str(e)[:160])
        return err("lookup failed", status_code=500)

    if not rows:
        return ok({"sent": 0, "skipped": len(urls), "reason": "rows no longer present"})

    wanted = set(urls)
    items = []
    for r in rows:
        u = r.get("chapter_url") or r.get("url") or ""
        if u not in wanted:
            continue
        items.append(
            {
                "title": r.get("title") or "",
                "title_key": r.get("title_key") or "",
                "chapter": r.get("chapter") or "",
                "source": r.get("source") or "",
                "url": u,
                "chapter_url": u,
                "series_url": r.get("series_url") or "",
                "cover": r.get("cover") or "",
                "description": r.get("description") or "",
                "genres": r.get("genres") or [],
                "rating": r.get("rating"),
                "type": r.get("type") or "",
                "origin": r.get("origin") or "",
                "release_date": r.get("release_date") or "",
                "updated_time": r.get("updated_time") or "",
            }
        )

    if not items:
        return ok({"sent": 0, "skipped": len(urls), "reason": "no matching rows"})

    # Guard: never resend a chapter that already has a ledger row. dispatch()
    # runs with force=True here, which bypasses the in-run URL guard, so this
    # explicit filter is the only thing stopping a double-post when the
    # dispatch_history fcfs_key/legacy-pair checks miss (e.g. NULL fcfs_key on
    # older rows). Fail CLOSED: if the lookup errors, resend nothing.
    try:
        from modules.manhwa.db import get_supabase
        from modules.manhwa.services.fcfs import fcfs_key

        _dh = (
            get_supabase()
            .table("dispatch_history")
            .select("chapter_url, fcfs_key")
            .in_("chapter_url", urls)
            .execute()
            .data
            or []
        )
        _sent_urls = {str(r.get("chapter_url")) for r in _dh if r.get("chapter_url")}
        _sent_fk = {str(r.get("fcfs_key")) for r in _dh if r.get("fcfs_key")}
        items = [
            it
            for it in items
            if it["url"] not in _sent_urls
            and fcfs_key(
                deslugify_title_key(str(it.get("title_key") or "")), it.get("chapter") or ""
            )
            not in _sent_fk
        ]
    except Exception as e:
        logger.warn("undelivered: resend already-sent guard failed", err=str(e)[:160])
        return err("already-sent check failed", status_code=500)

    if not items:
        return ok({"sent": 0, "requested": len(urls), "reason": "already sent"})

    try:
        from modules.manhwa.cron.dispatch_mod import dispatch as _dispatch, _load_channels

        channels = _load_channels()
        if not channels:
            return ok({"sent": 0, "reason": "no channels configured"})

        # Three explicit escape hatches, all required for this path:
        #   force         - transient claim + permanent URL guards
        #   skip_ceiling  - the chapter sits below its series ceiling
        #   bypass_window - the chapter is OLDER than 24h, so without this it
        #                   lands in the silent backfill bucket and gets a
        #                   ledger row but no Discord post. That was why these
        #                   chapters could be listed but never actually resent.
        # The dispatch_history dedupe still applies inside dispatch(): an
        # already-sent chapter returns 0, so a double-click cannot double-post.
        sent = _dispatch(
            items,
            channels,
            f"undelivered-resend-{int(time.time())}",
            force=True,
            skip_ceiling=True,
            bypass_window=True,
        )
        return JSONResponse(
            content={
                "success": True,
                "data": {"sent": int(sent), "requested": len(items), "skipped": len(urls) - len(items)},
            }
        )
    except Exception as e:
        logger.warn("undelivered: resend dispatch failed", err=str(e)[:200])
        return err("dispatch failed", status_code=500)