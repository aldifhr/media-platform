-- 061_drop_whitelist_final.sql — whitelist DROP fisik final (cover/rating/genres/desc/type/origin/status)

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
