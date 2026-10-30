-- Migration 035: Architecture fix 15→9 tables
-- Problem: 5 dead tables, 1 merged, nullable UNIQUE bugs, wrong PKs

-- 1. dashboard_snapshot: computed_at NOT NULL + singleton CHECK
UPDATE dashboard_snapshot SET computed_at = NOW() WHERE computed_at IS NULL;
ALTER TABLE dashboard_snapshot ALTER COLUMN computed_at SET NOT NULL;
ALTER TABLE dashboard_snapshot ADD CONSTRAINT dashboard_snapshot_singleton CHECK (id = 1);

-- 2. Backfill NULLs + NOT NULL (whitelist, excluded_titles, recent_chapters, dispatch_history)
UPDATE whitelist SET title_key = COALESCE(title_key, 'unknown-' || id::text) WHERE title_key IS NULL;
UPDATE whitelist SET source = COALESCE(source, 'ikiru') WHERE source IS NULL;
ALTER TABLE whitelist ALTER COLUMN title_key SET NOT NULL;
ALTER TABLE whitelist ALTER COLUMN source SET NOT NULL;

UPDATE excluded_titles SET title_key = COALESCE(title_key, 'unknown-' || id::text) WHERE title_key IS NULL;
UPDATE excluded_titles SET source = COALESCE(source, 'all') WHERE source IS NULL;
ALTER TABLE excluded_titles ALTER COLUMN title_key SET NOT NULL;
ALTER TABLE excluded_titles ALTER COLUMN source SET NOT NULL;

UPDATE recent_chapters SET chapter_url = COALESCE(chapter_url, 'unknown-' || id::text) WHERE chapter_url IS NULL;
UPDATE recent_chapters SET title_key = COALESCE(title_key, 'unknown-' || id::text) WHERE title_key IS NULL;
ALTER TABLE recent_chapters ALTER COLUMN chapter_url SET NOT NULL;
ALTER TABLE recent_chapters ALTER COLUMN title_key SET NOT NULL;

UPDATE dispatch_history SET chapter_url = COALESCE(chapter_url, 'unknown-' || ctid::text) WHERE chapter_url IS NULL;
ALTER TABLE dispatch_history ALTER COLUMN chapter_url SET NOT NULL;

-- 3. Indexes for RSS 24h queries
CREATE INDEX IF NOT EXISTS idx_recent_chapters_created_at ON recent_chapters(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recent_chapters_title_key ON recent_chapters(title_key);
CREATE INDEX IF NOT EXISTS idx_dispatch_history_sent_at ON dispatch_history(sent_at DESC);

-- 4. manga_metadata: composite PK + delete orphans
UPDATE manga_metadata SET source = 'ikiru' WHERE source IS NULL;
ALTER TABLE manga_metadata DROP CONSTRAINT IF EXISTS manga_metadata_pkey;
ALTER TABLE manga_metadata ADD PRIMARY KEY (title_key, source);
DELETE FROM manga_metadata WHERE title_key NOT IN (SELECT title_key FROM whitelist);

-- 5. DROP dead tables
ALTER TABLE guild_settings DROP COLUMN IF EXISTS excluded_titles;
DROP TABLE IF EXISTS series_max_chapter;
-- NOTE: these are KEPT (actively used by live code):
--   dispatch_claims → app/storage/dispatch.py (FCFS race-safety guard, prevents double-send)
--   failed_dispatches → app/services/dispatch_retry.py (retry queue for failed Discord sends)
--   canonical_series → dropped (dead, only 39 rows 11% coverage, code now self-canonical)
--   continue_reading → KEPT (route /api/continue-reading is live, used by FE)
DROP TABLE IF EXISTS canonical_series;

-- 6. Clean source_health
DELETE FROM source_health WHERE source NOT IN ('ikiru', 'shinigami');

-- 7. Prune cron_run_status (>90 days)
DELETE FROM cron_run_status WHERE created_at < NOW() - INTERVAL '90 days';
