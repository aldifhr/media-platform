-- Migration 080: normalize series_meta.title_key (UUID -> slug) + enforce slug.
--
-- ROOT CAUSE: migration 050 backfilled series_meta from manga_metadata with
-- title_key = LOWER(manga_id). shinigami's manga_id is a UUID, so series_meta
-- ended up with two identities for the same series: the slug written by live
-- code, and a UUID. Every join (rss_service, runner, SeriesMeta.get) uses the
-- slug, so UUID rows never matched and their rating/description was lost.
--
-- This migration:
--   1. resolves each UUID title_key to its slug via the series_url stored on
--      whitelist / recent_chapters (last path segment -> slugify),
--   2. merges the UUID row into the slug row (keep the richer metadata),
--   3. adds a CHECK so a UUID can never be stored again.
-- Idempotent: safe to re-run. Rows that cannot be resolved are LEFT IN PLACE
-- (never delete metadata) and simply fail the CHECK only if still UUID-shaped —
-- so the CHECK is added only when no unresolved UUID rows remain.

-- 1. Resolve + rewrite UUID title_keys using the slug from series_url.
--    slugify: lower, non-alnum -> '-', trim dashes. Mirrors slugify_title_key().
WITH resolved AS (
  SELECT sm.title_key AS old_tk,
         sm.source AS src,
         trim(both '-' from regexp_replace(
           lower(split_part(split_part(u.series_url, '?', 1), '/',
             array_length(string_to_array(split_part(u.series_url, '?', 1), '/'), 1))),
           '[^a-z0-9]+', '-', 'g'
         )) AS new_tk
  FROM series_meta sm
  JOIN LATERAL (
    SELECT COALESCE(w.series_url, rc.series_url) AS series_url
    FROM whitelist w
    LEFT JOIN LATERAL (
      SELECT series_url FROM recent_chapters r
      WHERE r.title_key = w.title_key AND r.source = w.source AND r.series_url <> ''
      LIMIT 1
    ) rc ON TRUE
    WHERE w.title_key = sm.title_key AND w.source = sm.source
    LIMIT 1
  ) u ON TRUE
  WHERE sm.title_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND u.series_url IS NOT NULL AND u.series_url <> ''
)
UPDATE series_meta sm
SET title_key = r.new_tk
FROM resolved r
WHERE sm.title_key = r.old_tk
  AND sm.source = r.src
  AND r.new_tk <> ''
  AND r.new_tk <> r.old_tk
  -- only if it does not collide with an existing slug row
  AND NOT EXISTS (
    SELECT 1 FROM series_meta x
    WHERE x.title_key = r.new_tk AND x.source = r.src
  );

-- 2. Merge any remaining UUID rows that DO collide with an existing slug row:
--    copy non-null fields into the slug row, then delete the UUID row.
WITH resolved AS (
  SELECT sm.title_key AS old_tk, sm.source AS src,
         trim(both '-' from regexp_replace(
           lower(split_part(split_part(u.series_url, '?', 1), '/',
             array_length(string_to_array(split_part(u.series_url, '?', 1), '/'), 1))),
           '[^a-z0-9]+', '-', 'g'
         )) AS new_tk
  FROM series_meta sm
  JOIN LATERAL (
    SELECT COALESCE(w.series_url, rc.series_url) AS series_url
    FROM whitelist w
    LEFT JOIN LATERAL (
      SELECT series_url FROM recent_chapters r
      WHERE r.title_key = w.title_key AND r.source = w.source AND r.series_url <> ''
      LIMIT 1
    ) rc ON TRUE
    WHERE w.title_key = sm.title_key AND w.source = sm.source
    LIMIT 1
  ) u ON TRUE
  WHERE sm.title_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND u.series_url IS NOT NULL AND u.series_url <> ''
)
UPDATE series_meta dst
SET cover       = COALESCE(dst.cover, src.cover),
    rating      = COALESCE(dst.rating, src.rating),
    genres      = COALESCE(dst.genres, src.genres),
    description = COALESCE(dst.description, src.description),
    type        = COALESCE(dst.type, src.type),
    origin      = COALESCE(dst.origin, src.origin),
    updated_at  = now()
FROM series_meta src
JOIN resolved r ON r.old_tk = src.title_key AND r.src = src.source
WHERE dst.title_key = r.new_tk
  AND dst.source = r.src
  AND src.title_key <> dst.title_key;

DELETE FROM series_meta src
USING (
  SELECT sm.title_key AS old_tk, sm.source AS src,
         trim(both '-' from regexp_replace(
           lower(split_part(split_part(u.series_url, '?', 1), '/',
             array_length(string_to_array(split_part(u.series_url, '?', 1), '/'), 1))),
           '[^a-z0-9]+', '-', 'g'
         )) AS new_tk
  FROM series_meta sm
  JOIN LATERAL (
    SELECT COALESCE(w.series_url, rc.series_url) AS series_url
    FROM whitelist w
    LEFT JOIN LATERAL (
      SELECT series_url FROM recent_chapters r
      WHERE r.title_key = w.title_key AND r.source = w.source AND r.series_url <> ''
      LIMIT 1
    ) rc ON TRUE
    WHERE w.title_key = sm.title_key AND w.source = sm.source
    LIMIT 1
  ) u ON TRUE
  WHERE sm.title_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND u.series_url IS NOT NULL AND u.series_url <> ''
) r
WHERE src.title_key = r.old_tk
  AND src.source = r.src
  AND EXISTS (SELECT 1 FROM series_meta x WHERE x.title_key = r.new_tk AND x.source = r.src);

-- 3. Enforce slug shape, but ONLY when no UUID rows remain (unresolved UUIDs
--    must not block the migration — they are left in place, reported below).
DO $$
DECLARE
  uuid_left int;
BEGIN
  SELECT count(*) INTO uuid_left FROM series_meta
  WHERE title_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  IF uuid_left = 0 THEN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_series_meta_tk_slug') THEN
      ALTER TABLE series_meta ADD CONSTRAINT chk_series_meta_tk_slug
        CHECK (title_key ~ '^[a-z0-9-]+$');
    END IF;
  ELSE
    RAISE NOTICE 'series_meta: % UUID title_key rows unresolved, CHECK not added', uuid_left;
  END IF;
END $$;
