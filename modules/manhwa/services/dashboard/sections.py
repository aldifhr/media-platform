"""Dashboard snapshot sections.

Each builder is self-contained and keeps the original error handling: a
failing section degrades to its empty value instead of taking the endpoint
down. Extracted from app/api/admin/stats.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

from modules.manhwa.services.dashboard.inputs import SnapshotInputs
from modules.manhwa.storage import whitelist as wl_store
from modules.manhwa.storage.series_meta import load_series_meta_map
from modules.manhwa.core.cover import scrub_cover
from modules.manhwa.core.text import normalize_shinigami_url as _norm_sh_url
from modules.manhwa.core.text import normalize_title_key as _ntk_q
from modules.manhwa.core.time import RECENT_WINDOW_HOURS, cutoff_iso


def build_recent_chapters(inp: SnapshotInputs) -> tuple[list[dict], int]:
    dh_rows = inp.dh_all.data or []
    dh_count = len(dh_rows)
    # recent 10 for the card
    dh_recent = dh_rows[:10]
    dh_urls = [r["chapter_url"] for r in dh_recent if r.get("chapter_url")]
    # Normalize shinigami URLs to current base so dispatch_history rows
    # still match recent_chapters after domain migration (f.shinigami.asia -> 11.shinigami.asia).
    dh_urls = [_norm_sh_url(u) or u for u in dh_urls]
    rc_by_url = {}
    wl_meta: dict[tuple[str, str], dict] = {}
    sm_map: dict[tuple[str, str], dict] = {}
    if dh_urls:
        rc = (
            inp.sb_client.table("recent_chapters")
            .select("chapter_url, title_key, title, chapter, cover, source, origin, series_url")
            .in_("chapter_url", dh_urls)
            .execute()
        )
        for r in rc.data or []:
            if r.get("chapter_url"):
                rc_by_url[r["chapter_url"]] = r
        # Enrich from whitelist (title_key, source only — static fields in series_meta)
        tks = [r.get("title_key") for r in (rc.data or []) if r.get("title_key")]
        if tks:
            try:
                wl_res = (
                    inp.sb_client.table("whitelist")
                    .select("title_key, source")
                    .in_("title_key", tks)
                    .execute()
                )
                for m in wl_res.data or []:
                    wl_meta[(m.get("title_key", ""), m.get("source", ""))] = m
            except Exception:
                pass
        # Get rating/description from series_meta
        if tks:
            try:
                sm_map.update(
                    load_series_meta_map(
                        inp.sb_client, tks, columns="title_key, source, rating, description"
                    )
                )
            except Exception:
                pass
    recent_chapters_list = []
    for r in dh_recent:
        _raw_url = r.get("chapter_url") or ""
        _norm_url = _norm_sh_url(_raw_url) or _raw_url
        rc = rc_by_url.get(_norm_url) or rc_by_url.get(_raw_url) or {}
        tk = rc.get("title_key") or r.get("title_key") or ""
        src = rc.get("source") or r.get("source") or ""
        meta = wl_meta.get((tk, src), {})
        title = rc.get("title") or r.get("chapter_title") or r.get("title_key") or "Untitled"
        # Avoid using raw chapter string as title when metadata is missing.
        if not rc and title not in (r.get("title_key") or ""):
            title = r.get("title_key") or title
        chapter = rc.get("chapter") or (r.get("chapter_title") or "")
        try:
            chapter_number = float(str(chapter).strip()) if str(chapter).strip() else 0.0
        except Exception:
            chapter_number = 0.0
        recent_chapters_list.append(
            {
                "title": title,
                "titleKey": rc.get("title_key") or r.get("title_key") or "",
                "chapterLabel": str(chapter),
                "chapterNumber": chapter_number,
                "source": src,
                "sentAt": r.get("sent_at") or "",
                "cover": scrub_cover(rc.get("cover")),
                "origin": rc.get("origin") or meta.get("origin") or "",
                "status": meta.get("status") or "",
                "rating": meta.get("rating") if meta.get("rating") is not None else "",
                "description": meta.get("description") or "",
                "chapterUrl": _norm_url or _raw_url or "",
                "seriesUrl": rc.get("series_url") or r.get("series_url") or "",
            }
        )
    return recent_chapters_list, dh_count


def build_queue_depth(inp: SnapshotInputs) -> int:
    # queue depth = whitelisted chapters in recent_chapters(24h) NOT yet notified
    try:
        from modules.manhwa.cron.collect import filter_whitelisted
        from modules.manhwa.cron.dispatch_mod import _claimed_titles, fcfs_key as _fk

        rc_24h_q = inp.rc_24h  # reuse parallel-fetched 24h window
        wl_rows = wl_store.load_whitelist()
        # Source-aware: a chapter counts as queued only if its
        # (title_key, source) is whitelisted. This matches the
        # actual dispatch path (filter_whitelisted) so the
        # shinigami for the same title don't double-count, and a
        # source the user did NOT whitelist is excluded.
        queued = filter_whitelisted(rc_24h_q, wl_rows) if wl_rows else []
        # Apply latest_sent ceiling (mirrors the send-phase + the
        # /failed-dispatches/queue endpoint): chapters at/below the
        # notified ceiling are NOT pending (already sent / will be
        # skipped by cron). Without this, stale low-numbered chapters
        # (e.g. Buka MBG ch11/13/14, already past latest_sent=27) sit in
        # recent_chapters and show as "pending" forever.
        _ceil_q: dict[tuple[str, str], float] = {}
        for w in wl_rows or []:
            tk = _ntk_q(w.get("title_key", ""))
            src = w.get("source") or ""
            try:
                _ls = float(w.get("latest_sent_chapter") or 0)
            except Exception:
                _ls = 0.0
            if tk:
                _ceil_q[(tk, src)] = max(_ceil_q.get((tk, src), 0), _ls)
        _pending_raw = []
        for c in queued:
            tk = _ntk_q(c.get("title_key", ""))
            src = c.get("source") or ""
            ceil = _ceil_q.get((tk, src), _ceil_q.get((tk, ""), 0))
            try:
                ch = float(c.get("chapter_num") or c.get("chapter") or 0)
            except Exception:
                ch = 0.0
            if ceil and ch <= ceil:
                continue
            _pending_raw.append(c)
        # Drop ones already sent — use FCFS key (normalized title+chapter),
        # NOT chapter_url. Sources rotate chapter URLs every scrape,
        # so URL-based matching misses chapters that WERE notified under a
        # different (older) URL → false "pending" in the queue depth.
        # This mirrors the actual dispatch path (_claimed_titles), so the
        # displayed queue depth matches what cron will/won't send.
        queued_keys = [_fk(c.get("title", ""), c.get("chapter", "")) for c in _pending_raw]
        claimed_keys = _claimed_titles(list(set(queued_keys))) if queued_keys else set()
        queued = [c for i, c in enumerate(_pending_raw) if queued_keys[i] not in claimed_keys]
        return len(queued)
    except Exception:
        return 0


def build_cron_averages(overview: dict, cron_stats: list) -> None:
    # avg cron duration + avg chapters/day from cron_run_status (per-run stats)
    try:
        stats_rows = cron_stats
        if stats_rows:
            total_sent = 0
            durations = []
            days = set()
            for s in stats_rows:
                try:
                    total_sent += int(s.get("chapters_sent") or 0)
                except (TypeError, ValueError):
                    pass
                dur = s.get("duration")
                if dur is not None:
                    try:
                        durations.append(float(dur))
                    except (TypeError, ValueError):
                        pass
                ts = s.get("created_at")
                if ts:
                    days.add(ts[:10])
            overview["avgCronDuration"] = (
                round(sum(durations) / len(durations), 1) if durations else 0.0
            )
            overview["averageChaptersPerDay"] = round(total_sent / max(1, len(days)), 1)
    except Exception:
        pass


def build_cron_status(cron_stats: list) -> tuple[dict | None, dict | None]:
    # cronStatus from most recent run (cron_stats[0] from parallel fetch).
    # NOTE: the latest cycle may legitimately have 0 sent/matched (no new
    # releases since the last fetch). That is NOT a failure — but showing
    # only the 0/0 cycle makes the panel look broken. So we also surface
    # lastDelivery: the most recent run that actually dispatched >0
    # chapters, proving the pipeline works. Frontend shows both.
    cron_status_data = None
    last_delivery = None
    try:
        if cron_stats:
            lr = cron_stats[0]
            # See cron_status_is_ok — the column arrives as a dict OR a JSON
            # string; the isinstance(dict) test alone missed the string form,
            # so outcome was always "error" and the navbar read the pipeline
            # as broken.
            from modules.manhwa.storage.health import cron_status_is_ok as _cron_ok

            _lr_ok = _cron_ok(lr.get("status"))
            cron_status_data = {
                "outcome": "ok" if _lr_ok else "error",
                "timestamp": lr.get("created_at")
                or lr.get("updated_at")
                or datetime.now(timezone.utc).isoformat(),
                "duration": lr.get("duration"),
                "matched": lr.get("matched") or 0,
                "sent": lr.get("chapters_sent") or 0,
            }
            for _r in cron_stats:
                try:
                    _sent = int(_r.get("chapters_sent") or 0)
                except (TypeError, ValueError):
                    _sent = 0
                if _sent > 0:
                    # See cron_status_is_ok — same JSONB-shape trap.
                    from modules.manhwa.storage.health import cron_status_is_ok as _cron_ok2

                    _r_ok = _cron_ok2(_r.get("status"))
                    last_delivery = {
                        "outcome": "ok" if _r_ok else "error",
                        "timestamp": _r.get("created_at")
                        or _r.get("updated_at")
                        or datetime.now(timezone.utc).isoformat(),
                        "duration": _r.get("duration"),
                        "matched": _r.get("matched") or 0,
                        "sent": _r.get("chapters_sent") or 0,
                    }
                    break
    except Exception:
        pass
    return cron_status_data, last_delivery


def build_chapters_by_source(sb_client) -> dict[str, int]:
    # Per-source breakdown (24h)
    chapters_by_source_24h: dict[str, int] = {}
    try:
        _src_rows = (
            sb_client.table("recent_chapters")
            .select("source")
            .gte(
                "updated_time",
                cutoff_iso(RECENT_WINDOW_HOURS),
            )
            .execute()
            .data
            or []
        )
        for r in _src_rows:
            src = str(r.get("source") or "unknown")
            chapters_by_source_24h[src] = chapters_by_source_24h.get(src, 0) + 1
    except Exception:
        pass
    return chapters_by_source_24h
