-- Migration 081: normalize series_meta.title_key UUID -> slug, the CORRECT way.
--
-- The previous attempt (080) joined series_meta to whitelist by title_key:
--     FROM whitelist w WHERE w.title_key = sm.title_key
-- but whitelist.title_key is slug-only (constraint chk_tk_slug, migration 051)
-- while sm.title_key is a UUID — so the join NEVER matched and 080 was a no-op.
--
-- The only table that carries BOTH identities for the same series is
-- recent_chapters: title_key = slug, series_url = '.../series/<UUID>'.
-- So we resolve UUID -> slug through recent_chapters.series_url.
--
-- Idempotent. Rows that cannot be resolved are LEFT IN PLACE (never delete
-- metadata); the CHECK is added only when no UUID rows remain.

-- 1. Rewrite UUID title_keys to the slug, using recent_chapters.series_url.
--    Extract the UUID from series_url and match it against series_meta.title_key.
WITH bridge AS (
  SELECT DISTINCT
    rc.source AS src,
    rc.title_key AS slug,
    substring(rc.series_url from '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})') AS uuid
  FROM recent_chapters rc
  WHERE rc.series_url ~ '/series/[0-9a-f-]{36}'
    AND rc.title_key <> ''
)
UPDATE series_meta sm
SET title_key = b.slug
FROM bridge b
WHERE sm.title_key = b.uuid
  AND sm.source = b.src
  AND b.slug <> ''
  AND b.slug <> sm.title_key
  -- only when it does not collide with an existing slug row
  AND NOT EXISTS (
    SELECT 1 FROM series_meta x
    WHERE x.title_key = b.slug AND x.source = b.src
  );

-- 2. Merge remaining UUID rows that DO collide with an existing slug row:
--    copy non-null fields into the slug row, then delete the UUID row.
WITH bridge AS (
  SELECT DISTINCT
    rc.source AS src,
    rc.title_key AS slug,
    substring(rc.series_url from '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})') AS uuid
  FROM recent_chapters rc
  WHERE rc.series_url ~ '/series/[0-9a-f-]{36}'
    AND rc.title_key <> ''
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
JOIN bridge b ON b.uuid = src.title_key AND b.src = src.source
WHERE dst.title_key = b.slug
  AND dst.source = b.src
  AND src.title_key <> dst.title_key;

DELETE FROM series_meta src
USING (
  SELECT DISTINCT
    rc.source AS src,
    rc.title_key AS slug,
    substring(rc.series_url from '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})') AS uuid
  FROM recent_chapters rc
  WHERE rc.series_url ~ '/series/[0-9a-f-]{36}'
    AND rc.title_key <> ''
) b
WHERE src.title_key = b.uuid
  AND src.source = b.src
  AND src.title_key <> b.slug
  AND EXISTS (SELECT 1 FROM series_meta x WHERE x.title_key = b.slug AND x.source = b.src);

-- 3. Enforce slug shape, only when no UUID rows remain.
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
