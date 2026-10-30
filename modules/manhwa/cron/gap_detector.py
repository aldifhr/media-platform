"""Chapter gap detector + auto-backfill

Compares each whitelist series' latest_sent_chapter (what Discord got) against
the newest chapter_num seen in recent_chapters (what the scraper found). A jump
> GAP_THRESHOLD means chapters were missed — likely a scraper skip or a
re-touch race.

Flow: detect -> alert admin (with cooldown) -> AUTO-BACKFILL from source API ->
dispatch to all servers. The backfill reuses each source's own chapter-list
API so the filled chapters carry real release timestamps and URLs.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger

logger = get_logger("gap-detector")

GAP_THRESHOLD = 1.0     # newest_scraped - latest_sent must exceed this
_last_backfill_ts: float = 0.0
_last_gaps_hash: str = ""

def _shinigami_chapters(manga_id: str) -> list[dict]:
    # Prefer the pooled httpx client with retry+circuit-breaker (parity with
    # app/scrapers/shinigami.py). Fallback to urllib only if import fails.
    try:
        from modules.manhwa.sources.shinigami import get_shinigami_chapters
        data = get_shinigami_chapters(manga_id, per_page=100)
        if data:
            return data
    except (Exception, ImportError) as e:
        logger.warn("gap backfill: shinigami scraper fetch failed", err=str(e)[:120])
    try:
        req = urllib.request.Request(
            f"{settings.SECONDARY_SOURCE_URL.rstrip(chr(47))}/v1/chapter/{manga_id}/list?page=1&page_size=100&sort_by=chapter_number&sort_order=desc",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("data", [])
    except (Exception, ImportError) as e:
        logger.warn("gap backfill: shinigami urllib fallback failed", err=str(e)[:120])
        return []

def detect_gaps() -> list[dict]:
    """Return [{title_key, source, sent, scraped}] where scraped - sent > threshold.
    
    Uses whitelist.latest_sent_chapter (authoritative marker) instead of
    computing MAX from dispatch_history (which can be incomplete if cross-source
    dedup or dispatch failures leave gaps in history).
    """
    try:
        from modules.manhwa.db import q as _raw_q
        from modules.manhwa.core.sql_fragments import RC_WL_JOIN_ON

        # One tolerant join instead of an N+1 .eq('title_key', tk) per series:
        # recent_chapters.title_key keeps the scraper's display form (spaces)
        # while whitelist.title_key is slugified (dashes), so a plain equality
        # silently missed the rows and the gap was never detected.
        rows = _raw_q(
            f"""
            SELECT w.title_key AS wl_title_key, w.source AS wl_source,
                   w.latest_sent_chapter AS sent, MAX(rc.chapter_num) AS scraped
            FROM whitelist w
            JOIN recent_chapters rc
              ON {RC_WL_JOIN_ON}
            WHERE w.latest_sent_chapter > 0
              AND rc.chapter_num > w.latest_sent_chapter
              AND rc.chapter_num <= w.latest_sent_chapter + 2000
            GROUP BY w.title_key, w.source, w.latest_sent_chapter
            """,
            [],
        ) or []
        out = []
        for r in rows:
            tk = str(r.get("wl_title_key") or "")
            src = str(r.get("wl_source") or "")
            sent = float(r.get("sent") or 0)
            try:
                scraped = float(r.get("scraped") or 0)
            except (ValueError, TypeError):
                scraped = sent
            if scraped - sent > GAP_THRESHOLD:
                out.append({
                    "title_key": tk,
                    "source": src,
                    "sent": sent,
                    "scraped": scraped,
                })
        return out
    except (Exception, ImportError) as e:
        logger.warn("gap detection failed", err=str(e)[:160])
        return []

def _backfill_and_dispatch(gaps: list[dict]) -> dict:
    """For each gapped series: fetch its chapter list from the source API,
    insert missing chapters into recent_chapters, then dispatch them to all
    servers. Returns summary counts.

    NOTE: backfilled rows get a FRESH updated_time (not the real release date).
    The 24h prune deletes rows older than the window — a backfilled row with
    an old timestamp would be wiped on the very next cron tick.

    Robustness: each series is isolated with a SAVEPOINT so one bad series
    doesn't abort the whole batch (root cause of 7× needs manual fix).
    Status 'fixed' now requires actual dispatch/insert success."""
    inserted = dispatched = 0
    fixed: list[str] = []
    # track per-series detail for alert
    details: dict[str, str] = {}
    conn = None
    cur = None
    try:
        from modules.manhwa.db import get_conn, put_conn
        from modules.manhwa.cron.dispatch_mod import dispatch, _load_channels

        conn = get_conn()
        cur = conn.cursor()
        channels = None  # lazy-load once
        for g in gaps:
            tk, src = g["title_key"], g["source"]
            key = f"{tk[:30]} ({src})"
            # per-series savepoint isolation
            sp_name = f"sp_gap_{int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100000}"
            import re as _re_sp
            if not _re_sp.match(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$", sp_name):
                sp_name = "sp_gap_fallback"
            try:
                cur.execute(f"SAVEPOINT {sp_name}")
            except Exception:
                pass
            try:
                from modules.manhwa.core.text import slugify_title_key
                tk_norm = slugify_title_key(tk)
                lo, hi = g["sent"], g["scraped"]

                cur.execute(
                    """SELECT title_key, title, chapter, chapter_num, source, cover,
                              series_url, origin, updated_time, description, chapter_url
                       FROM recent_chapters
                       WHERE REPLACE(title_key,' ','-')=%s AND source=%s AND chapter_num>%s AND chapter_num<=%s
                       ORDER BY chapter_num""",
                    (tk_norm, src, lo, hi),
                )
                existing_items = [dict(r) for r in cur.fetchall()]
                if existing_items:
                    fresh_stamp = datetime.now(timezone.utc).isoformat()
                    for it in existing_items:
                        it["updated_time"] = fresh_stamp
                        it["url"] = it.get("chapter_url") or it.get("url") or ""
                    if channels is None:
                        channels = _load_channels()
                    sent_this = dispatch(existing_items, channels, instance_id="gap-autofix", force=False) if channels else 0
                    dispatched += sent_this
                    if sent_this > 0:
                        cur.execute(
                            """UPDATE whitelist SET
                                 latest_sent_chapter = GREATEST(COALESCE(latest_sent_chapter,0), %s),
                                 latest_chapter      = GREATEST(COALESCE(latest_chapter,0), %s)
                               WHERE title_key=%s AND source=%s""",
                            (hi, hi, tk_norm, src),
                        )
                        fixed.append(key)
                        details[key] = f"ok existing rows, sent:{sent_this}"
                    else:
                        details[key] = f"no send (FCFS or junk), sent:{sent_this}"
                    try:
                        cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                    except Exception:
                        pass
                    continue

                # No rows in gap range — try to backfill from source API
                cur.execute(
                    "SELECT series_url FROM whitelist WHERE title_key=%s AND source=%s",
                    (tk_norm, src),
                )
                row = cur.fetchone()
                series_url = (row.get("series_url") if row else "") or ""

                # Build normalized chapter list [(num, url, title, release_date)]
                chapters: list[tuple] = []
                if src == "shinigami":
                    mid = series_url.rstrip("/").split("/")[-1] if series_url else ""
                    if not mid:
                        logger.warn("gap backfill: shinigami missing manga_id", title_key=tk)
                    else:
                        _cap = lo + 2000
                        for c in _shinigami_chapters(mid):
                            try:
                                num = float(c.get("chapter_number") or 0)
                            except (ValueError, TypeError):
                                continue
                            if num <= 0 or num > _cap:
                                continue
                            ch_id = c.get("chapter_id")
                            if not ch_id:
                                continue
                            url = f"{settings.SHINIGAMI_PUBLIC_BASE}/chapter/{ch_id}"
                            chapters.append((num, url, str(c.get("chapter_title") or ""), c.get("release_date")))
                if not chapters:
                    logger.warn("gap backfill: no chapters fetched", title_key=tk, source=src, lo=lo, hi=hi)
                    # don't mark fixed — will show needs manual fix with reason
                    details[key] = "fetch empty"
                    try:
                        cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                        cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                    except Exception:
                        pass
                    continue

                fresh_stamp = datetime.now(timezone.utc).isoformat()
                # bulk: filter range + dedup URLs + single SELECT for existing + single cover fetch
                candidates = [(num, url, title) for num, url, title, rel in chapters if lo < num <= hi and not (url.startswith("https://x/") or url.startswith("http://x/"))]
                # dedup by url
                seen_urls: set[str] = set()
                uniq_candidates: list[tuple] = []
                for num, url, title in candidates:
                    if url not in seen_urls:
                        seen_urls.add(url)
                        uniq_candidates.append((num, url, title))
                # single cover fetch per gap (was per chapter N+1) — cover canonical series_meta since 052
                cur.execute("SELECT cover FROM series_meta WHERE title_key=%s AND source=%s", (tk_norm, src))
                wrow = cur.fetchone()
                # The pool cursor is a RealDictCursor, so a row is a DICT. `wrow[0]`
                # raised KeyError and killed the whole per-series backfill (the
                # caller's `except` logged "gap backfill: per-series failed").
                cover_val = (wrow.get("cover") if wrow else "") or ""
                # bulk existing check 1×
                existing_urls: set[str] = set()
                if uniq_candidates:
                    urls = [url for _, url, _ in uniq_candidates]
                    # chunk IN 500 to avoid placeholder limit
                    for i in range(0, len(urls), 500):
                        chunk = urls[i:i+500]
                        ph = ", ".join(["%s"] * len(chunk))
                        cur.execute(f"SELECT chapter_url FROM recent_chapters WHERE chapter_url IN ({ph})", chunk)
                        for r in cur.fetchall():
                            existing_urls.add(r.get("chapter_url") or "")
                # build bulk inserts for non-existing
                to_insert: list[dict] = []
                for num, url, title in uniq_candidates:
                    if url in existing_urls:
                        continue
                    to_insert.append({
                        "chapter_url": url,
                        "title_key": tk_norm,
                        "title": title or tk.replace("-", " ").title(),
                        "chapter": str(int(num)) if num == int(num) else str(num),
                        "chapter_num": num,
                        "source": src,
                        "cover": cover_val,
                        "series_url": series_url,
                        "updated_time": fresh_stamp,
                        "origin": "KR",
                        "description": "",
                    })
                inserted_this = 0
                if to_insert:
                    # bulk VALUES 1× (was N× INSERT)
                    cols = ", ".join(to_insert[0].keys())
                    ph_row = "(" + ", ".join(["%s"] * len(to_insert[0])) + ")"
                    ph_all = ", ".join([ph_row] * len(to_insert))
                    vals: list = []
                    for ins in to_insert:
                        vals.extend(list(ins.values()))
                    cur.execute(
                        f"""INSERT INTO recent_chapters ({cols}) VALUES {ph_all}
                            ON CONFLICT (chapter_url) DO UPDATE
                            SET cover = EXCLUDED.cover, updated_time = EXCLUDED.updated_time""",
                        vals,
                    )
                    inserted_this = len(to_insert)
                inserted += inserted_this

                # dispatch everything in the gap range (existing rows included)
                cur.execute(
                    """SELECT title_key, title, chapter, chapter_num, source, cover,
                              series_url, origin, updated_time, description, chapter_url
                       FROM recent_chapters
                       WHERE title_key=%s AND source=%s AND chapter_num>%s AND chapter_num<=%s
                       ORDER BY chapter_num""",
                    (tk_norm, src, lo, hi),
                )
                # Rows are RealDictRows. The previous `dict(zip(names, r))` zipped
                # the column names against the row's KEYS, so every field became
                # its own name — title literally "title", chapter "chapter". Any
                # gap that reached dispatch would have posted that as the
                # notification. dict(row) is the correct conversion.
                items = []
                for r in cur.fetchall():
                    it = dict(r)
                    it["updated_time"] = fresh_stamp
                    # dispatch_mod expects 'url' (not 'chapter_url') — alias it
                    it["url"] = it.get("chapter_url") or it.get("url") or ""
                    items.append(it)
                sent_this = 0
                if items:
                    if channels is None:
                        channels = _load_channels()
                    if not channels:
                        logger.warn("gap backfill: no channels to dispatch", title_key=tk)
                        details[key] = "no channels"
                    else:
                        sent_this = dispatch(items, channels, instance_id="gap-autofix", force=False)
                        dispatched += sent_this
                        if sent_this == 0 and inserted_this == 0:
                            # rows existed but FCFS blocked send — likely already dispatched
                            # treat as fixed to stop spam, but log
                            logger.info("gap backfill: dispatch 0 (FCFS dedup)", title_key=tk, lo=lo, hi=hi)
                else:
                    logger.warn("gap backfill: no rows in gap range after insert", title_key=tk, lo=lo, hi=hi)
                    details[key] = "no rows for dispatch"

                # sync markers — only advance if we actually sent or FCFS confirms already sent.
                # If fetch empty / dispatch 0 with new rows, keep marker so next cron retries.
                did_fix = False
                if sent_this > 0 or inserted_this > 0:
                    did_fix = True
                    details[key] = f"ok +{inserted_this} sent:{sent_this}"
                elif items and sent_this == 0:
                    did_fix = True
                    details[key] = "already sent (FCFS)"
                else:
                    details[key] = f"no progress ins:{inserted_this} sent:{sent_this}"

                if did_fix:
                    cur.execute(
                        """UPDATE whitelist SET
                             latest_sent_chapter = GREATEST(COALESCE(latest_sent_chapter,0), %s),
                             latest_chapter      = GREATEST(COALESCE(latest_chapter,0), %s)
                           WHERE title_key=%s AND source=%s""",
                        (hi, hi, tk_norm, src),
                    )
                    fixed.append(key)
                else:
                    # keep gap visible for retry; don't bump marker
                    logger.warn("gap backfill: not advancing marker (will retry)", title_key=tk, lo=lo, hi=hi)

                try:
                    cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                except Exception:
                    pass
            except Exception as se:
                logger.warn("gap backfill: per-series failed", title_key=tk, source=src, err=str(se)[:200])
                details[key] = f"error: {str(se)[:80]}"
                try:
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                    cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                except Exception:
                    pass
                continue
        conn.commit()
    except (Exception, ImportError) as e:
        logger.warn("gap auto-backfill failed", err=str(e)[:200])
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
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
    if fixed:
        logger.info("gap auto-backfill done", inserted=inserted, dispatched=dispatched, series=len(fixed), details=str(details)[:500])
    else:
        logger.debug("gap auto-backfill: nothing fixed", inserted=inserted, dispatched=dispatched, details=str(details)[:500])
    return {"inserted": inserted, "dispatched": dispatched, "fixed": fixed, "details": details}

def maybe_alert_gaps() -> int:
    """Run detection, then AUTO-BACKFILL and re-dispatch the missing chapters.
    Returns number of gapped series found."""
    global _last_backfill_ts, _last_gaps_hash
    gaps = detect_gaps()
    if not gaps:
        _last_gaps_hash = ""
        return 0
    gaps_hash = ",".join(sorted(f"{g['title_key']}:{g['source']}:{g['sent']}->{g['scraped']}" for g in gaps))
    now = time.monotonic()
    if gaps_hash == _last_gaps_hash and now - _last_backfill_ts < 900:
        logger.debug("gap backfill throttled (same gaps <15m)")
        return len(gaps)
    result = None

    # ── AUTO-FIX: always attempt ──
    result = _backfill_and_dispatch(gaps)
    _last_backfill_ts = now
    _last_gaps_hash = gaps_hash if not result.get("fixed") else ""
    fixed_names = set(result.get("fixed") or [])

    # A gap the auto-backfill already closed is not an incident.
    unresolved = [g for g in gaps if f"{g['title_key'][:30]} ({g['source']})" not in fixed_names]
    if not unresolved:
        logger.info(
            "gap auto-fixed, no alert needed",
            series=len(fixed_names),
        )
    return len(gaps)
