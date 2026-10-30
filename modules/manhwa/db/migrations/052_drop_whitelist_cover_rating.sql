-- 052_drop_whitelist_cover_rating.sql — whitelist minimal (drop cover/rating)

-- Backfill series_meta from whitelist where cover/rating not null, then deprecate columns (no DROP yet, just COMMENT)

-- Backfill series_meta from whitelist (only rows with static data)
INSERT INTO series_meta (title_key, source, cover, rating, genres, description, type, origin, updated_at)
SELECT
  w.title_key,
  w.source,
  w.cover,
  w.rating,
  w.genres,
  w.description,
  w.type,
  w.origin,
  now()
FROM whitelist w
WHERE w.cover IS NOT NULL
   OR w.rating IS NOT NULL
   OR w.genres IS NOT NULL
   OR w.description IS NOT NULL
   OR w.type IS NOT NULL
   OR w.origin IS NOT NULL
ON CONFLICT (title_key, source) DO UPDATE SET
  cover       = COALESCE(EXCLUDED.cover, series_meta.cover),
  rating      = COALESCE(EXCLUDED.rating, series_meta.rating),
  genres      = COALESCE(EXCLUDED.genres, series_meta.genres),
  description = COALESCE(EXCLUDED.description, series_meta.description),
  type        = COALESCE(EXCLUDED.type, series_meta.type),
  origin      = COALESCE(EXCLUDED.origin, series_meta.origin),
  updated_at  = now();

't DROP COLUMN yet (back-compat), just COMMENT deprecated — code no longer SELECTs them, drop in 053 after 1 week stable
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='cover') THEN
    COMMENT ON COLUMN whitelist.cover IS 'deprecated: canonical is series_meta.cover; kept for back-compat, rss prioritizes sm>it>wl (052)';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='rating') THEN
    COMMENT ON COLUMN whitelist.rating IS 'deprecated: canonical is series_meta.rating; kept for back-compat (052)';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='genres') THEN
    COMMENT ON COLUMN whitelist.genres IS 'deprecated: canonical is series_meta.genres (052)';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='description') THEN
    COMMENT ON COLUMN whitelist.description IS 'deprecated: canonical is series_meta.description (052)';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='type') THEN
    COMMENT ON COLUMN whitelist.type IS 'deprecated: canonical is series_meta.type (052)';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='origin') THEN
    COMMENT ON COLUMN whitelist.origin IS 'deprecated: canonical is series_meta.origin (052)';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='status') THEN
    COMMENT ON COLUMN whitelist.status IS 'deprecated: canonical is series_meta (052)';
  END IF;
END $$;
