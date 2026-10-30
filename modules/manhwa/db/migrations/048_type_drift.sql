-- 048_type_drift.sql
-- Fix drift: source CHECK, rating unify, continue_reading updated_at, FK docs

-- 1. CHECK source di semua table yang punya source (app jaga _VALID_SOURCES, DB jaga juga)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='chk_whitelist_source') THEN
    ALTER TABLE whitelist ADD CONSTRAINT chk_whitelist_source CHECK (source IN ('ikiru','shinigami','voratoon',''));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='chk_recent_chapters_source') THEN
    ALTER TABLE recent_chapters ADD CONSTRAINT chk_recent_chapters_source CHECK (source IN ('ikiru','shinigami','voratoon',''));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='chk_excluded_titles_source') THEN
    ALTER TABLE excluded_titles ADD CONSTRAINT chk_excluded_titles_source CHECK (source IN ('ikiru','shinigami','voratoon','all',''));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='chk_source_health_source') THEN
    ALTER TABLE source_health ADD CONSTRAINT chk_source_health_source CHECK (source IN ('ikiru','shinigami','voratoon'));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='chk_chapter_bookmarks_source') THEN
    ALTER TABLE chapter_bookmarks ADD CONSTRAINT chk_chapter_bookmarks_source CHECK (source IN ('ikiru','shinigami','voratoon',''));
  END IF;
END $$;

-- 2. Unify rating type: whitelist.rating text -> double precision biar sama kayak series_meta/recent_chapters
--    041 sudah migrate ke float, tapi cek lagi kalau masih text
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='rating' AND data_type='text') THEN
    ALTER TABLE whitelist ALTER COLUMN rating TYPE double precision USING NULLIF(rating,'')::double precision;
  END IF;
END $$;

-- 3. continue_reading.updated_at double -> timestamptz (epoch) biar index sargable
--    Data existing double epoch, convert via to_timestamp
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='continue_reading' AND column_name='updated_at' AND data_type='double precision') THEN
    ALTER TABLE continue_reading ALTER COLUMN updated_at TYPE timestamptz USING to_timestamp(updated_at);
    CREATE INDEX IF NOT EXISTS idx_continue_reading_updated_at_tz ON continue_reading (updated_at DESC);
  END IF;
END $$;

-- 4. FK docs — flat-per-source design sengaja tanpa FK (CONTEXT.md), tapi dispatch_claims harus cascade biar gak orphan
COMMENT ON TABLE recent_chapters IS 'Flat-per-source (title_key,source) — intentional no FK to whitelist, see CONTEXT.md';
COMMENT ON TABLE dispatch_history IS 'No FK to whitelist/recent_chapters — flat, orphan allowed, pruned by retention';
COMMENT ON TABLE dispatch_claims IS 'FK should cascade when recent_chapters pruned — add ON DELETE CASCADE via periodic reaper if needed';
