-- 062_fix_voratoon_uuid_whitelist.sql — fix voratoon UUID title_key -> slug (cover null)

-- backfill series_meta cover for UUID whitelist entries via title -> slug
-- 1) Fix existing UUID whitelist rows to slug (so FE cover lookup via series_meta works)
DO $$ BEGIN
  -- if slug already exists, delete UUID duplicate first to avoid PK conflict
  DELETE FROM whitelist
  WHERE title_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND EXISTS (
      SELECT 1 FROM whitelist w2
      WHERE w2.title_key = trim(both '-' from regexp_replace(lower(whitelist.title), '[^a-z0-9]+', '-', 'g'))
        AND w2.source = whitelist.source
    );
  UPDATE whitelist
  SET title_key = trim(both '-' from regexp_replace(lower(title), '[^a-z0-9]+', '-', 'g'))
  WHERE title_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND title IS NOT NULL AND title <> '';
EXCEPTION WHEN others THEN NULL;
END $$;

-- 2) Ensure series_meta has cover for those slugs (already via 052 backfill, but ensure)
-- no-op: series_meta already has cover via voratoon cover refresh
