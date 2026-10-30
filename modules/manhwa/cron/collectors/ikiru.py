"""Ikiru per-series collector.

Fetches page 1 of the project manga list (24 series) and extracts the
embedded chapter list from each.

The listing is only a DISCOVERY pass, never the source of truth for a
subscribed series. Two measured reasons:

  - /manga/project?page=2 returns 0 series, so series beyond the first 24
    of 147 are unreachable through the listing at all. Live: 3 of 13
    whitelisted titles never appeared in the 24, and one title's newest
    release could not be seen until its detail endpoint was called
    directly.
  - The listing embeds only 3 chapters per series and its timestamps lag:
    the newest embedded chapter across all 24 was dated 2026-10-09 while
    per-series detail reported fresh releases the same hour.

So every whitelisted title is ALSO polled through its own detail
endpoint, which returns latestChapter {id, number} plus lastUpdated. That
is the path that makes a subscribed release reliably visible; the listing
only contributes descriptions and metadata for titles that happen to be
on page 1.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from modules.manhwa.config import settings
from modules.manhwa.cron.collectors.common import build_item, whitelisted_for_source
from modules.manhwa.logger import get_logger
from modules.manhwa.services.fcfs import parse_chapter_number as _parse_chapter_num
from modules.manhwa.core.rating_utils import normalize_rating
from modules.manhwa.core.text import slugify_title_key

logger = get_logger("cron:collect:ikiru")

SOURCE = "ikiru"
MAX_PAGES = 1
MAX_CHAPTERS_PER_SERIES = 25


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _whiteliked_series() -> list[tuple[str, str]]:
    """(title_key, slug) for every whitelisted ikiru series.

    The whitelist stores the slugified title as title_key, and ikiru's slug
    is that same slugified title — verified 13/13 live, so the title_key is
    usable as the detail-path slug directly.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for r in whitelisted_for_source(SOURCE):
        tk = str(r.get("title_key") or "").strip()
        if tk and tk not in seen:
            seen.add(tk)
            out.append((tk, tk))
    return out


def _collect_ikiru_source(latest_sent: dict, disabled: set, fetch_meta: bool = True) -> list[dict]:
    from modules.manhwa.sources import ikiru as ik

    try:
        lookback = int(getattr(settings, "RSS_LOOKBACK_HOURS", 24))
    except Exception:
        lookback = 24
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)

    items: list[dict] = []

    # ── Pass A: listing (discovery + metadata) ──
    for page in range(1, MAX_PAGES + 1):
        data = ik.get_ikiru_project_page(page=page)
        if not data:
            break

        projects = data.get("data", {}).get("project", [])
        if not projects:
            break

        for series in projects:
            if not isinstance(series, dict):
                continue

            title = (series.get("title") or "").strip()
            if not title:
                continue

            slug = series.get("slug") or ""
            if not slug:
                continue

            tk = slugify_title_key(title)
            series_url = ik.series_url_for(slug)

            # Derive type/origin from the type field
            raw_type = str(series.get("type") or "").strip()
            content_type, origin = ik._derive_format(raw_type)

            cover = series.get("featuredImage") or ""
            rating = 0.0
            meta = series.get("metadata") or {}
            if meta.get("score") not in (None, ""):
                rating = normalize_rating(meta.get("score")) or 0.0

            genres = [g.get("name", "") for g in (meta.get("genre") or []) if isinstance(g, dict) and g.get("name")]

            # Check if any chapter is fresh before fetching per-series detail
            chapters = series.get("chapter") or []
            if not isinstance(chapters, list):
                chapters = []

            ceiling = latest_sent.get((tk, SOURCE), 0)
            fresh_chapters = []
            for ch in chapters:
                if not isinstance(ch, dict):
                    continue
                ch_num_raw = ch.get("number")
                if ch_num_raw is None:
                    continue
                dt = _parse_ts(ch.get("updatedAt"))
                if dt is None or dt < cutoff:
                    continue
                num = _parse_chapter_num(str(ch_num_raw))
                if num is not None and ceiling and num <= ceiling:
                    continue
                fresh_chapters.append((ch_num_raw, dt, num, ch))

            if not fresh_chapters:
                continue

            # Fetch per-series detail for description + full chapter list
            detail = ik.get_ikiru_series_detail(slug)
            description = ""
            if detail:
                import html as _html
                raw_desc = str(detail.get("description") or "").strip()
                if raw_desc:
                    # Strip HTML tags
                    import re as _re
                    description = _re.sub(r"<[^>]+>", "", raw_desc).strip()
                    description = _html.unescape(description)

            kept = 0
            for ch_num_raw, dt, num, ch in fresh_chapters:
                if kept >= MAX_CHAPTERS_PER_SERIES:
                    break

                ch_str = str(ch_num_raw)
                chapter_url = ik.chapter_url_for(slug, ch_num_raw, ch.get("slug") or "")
                kept += 1

                items.append(build_item(
                    title=title,
                    title_key=tk,
                    chapter=ch_str,
                    chapter_num=num,
                    url=chapter_url,
                    source=SOURCE,
                    cover=cover,
                    series_url=series_url,
                    chapter_url=chapter_url,
                    origin=origin,
                    updated_time=dt.isoformat(),
                    release_date=dt.isoformat(),
                    rating=rating,
                    genres=genres,
                    description=description,
                    type=content_type,
                ))

        logger.info(
            "ikiru collect done",
            items=len(items),
            page=page,
            series=len(projects),
        )

    # ── Pass B: direct poll of every whitelisted series ──
    #
    # The listing cannot be trusted to surface a subscribed release: page 2
    # is empty, and the embedded chapter timestamps lag the detail endpoint.
    # For each whitelisted title we read latestChapter + lastUpdated
    # directly and emit a chapter item when it is inside the window and above
    # the ceiling. Items already emitted by Pass A are skipped, so a title
    # visible in both places produces one item, not two.
    wl_series = _whiteliked_series()
    if wl_series:
        import concurrent.futures

        _have = {(i["title_key"], i["chapter"]) for i in items}

        def _poll(tk: str) -> dict | None:
            # One title's failure must not drop the whole whitelist poll:
            # ThreadPoolExecutor.map re-raises a worker exception into the
            # consumer loop, so an unguarded _poll lost every other title's
            # result too. Isolate per item and leave a trace.
            try:
                return _poll_inner(tk)
            except Exception as _pe:
                logger.warn("ikiru whitelist poll failed for one title — skipped", title_key=tk, err=str(_pe)[:160])
                return None

        def _poll_inner(tk: str) -> dict | None:
            d = ik.get_ikiru_series_detail(tk)
            dd = (d or {}).get("data", d) if d else None
            if not isinstance(dd, dict):
                return None
            lc = dd.get("latestChapter") or {}
            ch_num = lc.get("number")
            if ch_num is None:
                return None
            lu = _parse_ts(dd.get("lastUpdated"))
            if lu is None or lu < cutoff:
                return None
            if (tk, str(ch_num)) in _have:
                return None
            num = _parse_chapter_num(str(ch_num))
            ceiling = latest_sent.get((tk, SOURCE), 0)
            if num is not None and ceiling and num <= ceiling:
                return None
            meta = dd.get("metadata") or {}
            rating = 0.0
            if meta.get("score") not in (None, ""):
                rating = normalize_rating(meta.get("score")) or 0.0
            genres = [
                g.get("name", "")
                for g in (meta.get("genre") or [])
                if isinstance(g, dict) and g.get("name")
            ]
            import html as _html
            import re as _re

            desc = _re.sub(r"<[^>]+>", "", str(dd.get("description") or "")).strip()
            desc = _html.unescape(desc)
            content_type, origin = ik._derive_format(str(dd.get("type") or "").strip())
            slug = str(dd.get("slug") or tk)
            url = ik.chapter_url_for(slug, ch_num, "")
            return build_item(
                title=str(dd.get("title") or tk),
                title_key=tk,
                chapter=str(ch_num),
                chapter_num=num,
                url=url,
                source=SOURCE,
                cover=str(dd.get("featuredImage") or ""),
                series_url=ik.series_url_for(slug),
                chapter_url=url,
                origin=origin,
                updated_time=lu.isoformat(),
                release_date=lu.isoformat(),
                rating=rating,
                genres=genres,
                description=desc,
                type=content_type,
            )

        # ponytail: 6 workers. Raise only if a poll cycle gets slower than the
        # 120s source timeout; ikiru has not rate-limited us at 13 titles.
        _poll_added = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as _ex:
            for _res in _ex.map(lambda tk: _poll(tk), [t for t, _ in wl_series]):
                if _res:
                    items.append(_res)
                    _poll_added += 1
        logger.info(
            "ikiru whitelist poll done",
            polled=len(wl_series),
            added=_poll_added,
            total_items=len(items),
        )

    return items
