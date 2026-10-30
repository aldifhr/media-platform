-- 054_drop_whitelist_deprecated_cols.sql — fisik drop whitelist static cols (8+ -> 9)

-- prereq: 052 backfill + COMMENT sudah, 053 v_series sudah

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='cover') THEN
    ALTER TABLE whitelist DROP COLUMN cover;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='rating') THEN
    ALTER TABLE whitelist DROP COLUMN rating;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='genres') THEN
    ALTER TABLE whitelist DROP COLUMN genres;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='description') THEN
    ALTER TABLE whitelist DROP COLUMN description;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='type') THEN
    ALTER TABLE whitelist DROP COLUMN type;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='origin') THEN
    ALTER TABLE whitelist DROP COLUMN origin;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='status') THEN
    ALTER TABLE whitelist DROP COLUMN status;
  END IF;
END $$;

COMMENT ON TABLE whitelist IS 'minimal: title_key, source, series_url, latest_sent_chapter, title, created_at (054) — static canonical in series_meta/v_series';
