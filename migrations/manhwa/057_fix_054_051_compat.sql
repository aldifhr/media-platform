-- 057_fix_054_051_compat.sql — hotfix for 054 drop + 051 CONCURRENTLY

-- 054 dropped whitelist cover/rating/genres etc but many SELECTs still need them -> add back as deprecated (no DROP, just ensure exists)
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS cover TEXT;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS rating FLOAT;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS genres JSONB;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS type TEXT;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS origin TEXT;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS status TEXT;

-- 051 CONCURRENTLY failed in transaction -> ensure rc_composite exists without CONCURRENTLY
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='rc_composite') THEN
    CREATE UNIQUE INDEX rc_composite ON recent_chapters(title_key, source, chapter_num) WHERE chapter_num <> 0;
  END IF;
END $$;

-- dispatch_claims was DROPped in 055 but code still queries it (claim precheck) -> recreate as UNLOGGED for compat (will be unused but prevents 500)
CREATE TABLE IF NOT EXISTS dispatch_claims (
  title_key TEXT,
  chapter_url TEXT,
  fcfs_key TEXT,
  created_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  status TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS dispatch_claims_fcfs_key_key ON dispatch_claims(fcfs_key);
CREATE UNIQUE INDEX IF NOT EXISTS dispatch_claims_chapter_url_key ON dispatch_claims(chapter_url);
