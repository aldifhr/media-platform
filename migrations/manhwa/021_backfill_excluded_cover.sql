-- Migration 021: backfill excluded_titles cover/series_url from whitelist/catalog
-- We join excluded_titles.title_key with whitelist.title_key+source to pull
-- existing cover/series_url without touching upstream scrapers.
--
-- If future excluded rows need fresh data they will get it from later syncs
-- or manual FE operations; this is a one-shot historical fill.

WITH wl AS (
  SELECT title_key, source, cover, series_url
  FROM public.whitelist
)
UPDATE public.excluded_titles e
SET
  cover = wl.cover,
  series_url = wl.series_url
FROM wl
WHERE e.title_key = wl.title_key
  AND e.source = wl.source
  AND wl.cover IS NOT NULL
  AND wl.cover <> '';
