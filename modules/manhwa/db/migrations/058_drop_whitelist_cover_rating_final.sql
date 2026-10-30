-- 058_drop_whitelist_cover_rating_final.sql — fisik drop cover/rating final (052 minimal + 057 compat done)

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='cover') THEN
    ALTER TABLE whitelist DROP COLUMN cover;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='whitelist' AND column_name='rating') THEN
    ALTER TABLE whitelist DROP COLUMN rating;
  END IF;
END $$;

COMMENT ON TABLE whitelist IS 'minimal: title_key,source,series_url,latest_sent,title,created_at (058) — cover/rating canonical in series_meta';
