-- Add komiku to source CHECK constraints (was: ikiru, shinigami, voratoon)
DO $$ BEGIN
  -- Drop old constraints if they exist
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='chk_recent_chapters_source') THEN
    ALTER TABLE recent_chapters DROP CONSTRAINT chk_recent_chapters_source;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='chk_whitelist_source') THEN
    ALTER TABLE whitelist DROP CONSTRAINT chk_whitelist_source;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='chk_excluded_titles_source') THEN
    ALTER TABLE excluded_titles DROP CONSTRAINT chk_excluded_titles_source;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='chk_source_health_source') THEN
    ALTER TABLE source_health DROP CONSTRAINT chk_source_health_source;
  END IF;
  -- Add new constraints with komiku
  ALTER TABLE recent_chapters ADD CONSTRAINT chk_recent_chapters_source CHECK (source IN ('shinigami','komiku','voratoon',''));
  ALTER TABLE whitelist ADD CONSTRAINT chk_whitelist_source CHECK (source IN ('shinigami','komiku','voratoon',''));
  ALTER TABLE excluded_titles ADD CONSTRAINT chk_excluded_titles_source CHECK (source IN ('shinigami','komiku','voratoon','all',''));
  ALTER TABLE source_health ADD CONSTRAINT chk_source_health_source CHECK (source IN ('shinigami','komiku','voratoon'));
END $$;
