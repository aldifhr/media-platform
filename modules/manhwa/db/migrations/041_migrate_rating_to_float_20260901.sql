-- Migration 041 (2026-09-01): migrate rating columns from TEXT to FLOAT
-- Applied manually in production; included here for fresh DB setups.
BEGIN;

-- whitelist: TEXT -> FLOAT
ALTER TABLE whitelist
  ALTER COLUMN rating TYPE FLOAT
  USING NULLIF(rating, '')::FLOAT;

-- series_meta: TEXT -> FLOAT
ALTER TABLE series_meta
  ALTER COLUMN rating TYPE FLOAT
  USING NULLIF(rating, '')::FLOAT;

-- recent_chapters: drop default, convert empty strings, then FLOAT
ALTER TABLE recent_chapters ALTER COLUMN rating DROP DEFAULT;
UPDATE recent_chapters SET rating = NULL WHERE rating = '';
ALTER TABLE recent_chapters ALTER COLUMN rating TYPE FLOAT;
ALTER TABLE recent_chapters ALTER COLUMN rating SET DEFAULT 0.0;

COMMIT;
