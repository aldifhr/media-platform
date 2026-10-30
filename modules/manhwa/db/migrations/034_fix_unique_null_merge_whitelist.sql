-- Migration 034: Fix UNIQUE + NULL, merge whitelist_entries, fix PKs, add indexes
-- Problem: UNIQUE constraints allow NULL duplicates, whitelist_entries duplicate table,
--          manga_metadata PK wrong, missing indexes for RSS queries

-- 1. Fix UNIQUE + NULL: ALTER COLUMN SET NOT NULL
ALTER TABLE whitelist ALTER COLUMN title_key SET NOT NULL;
ALTER TABLE whitelist ALTER COLUMN source SET NOT NULL;
ALTER TABLE excluded_titles ALTER COLUMN title_key SET NOT NULL;
ALTER TABLE excluded_titles ALTER COLUMN source SET NOT NULL;
ALTER TABLE recent_chapters ALTER COLUMN chapter_url SET NOT NULL;
ALTER TABLE recent_chapters ALTER COLUMN title_key SET NOT NULL;
ALTER TABLE dispatch_history ALTER COLUMN chapter_url SET NOT NULL;
ALTER TABLE failed_dispatches ALTER COLUMN chapter_url SET NOT NULL;
ALTER TABLE manga_metadata ALTER COLUMN source SET NOT NULL;

-- 2. Merge whitelist_entries → whitelist, then DROP
INSERT INTO whitelist (title_key, source, created_at)
SELECT title_key, COALESCE(source, 'ikiru'), now()
FROM whitelist_entries
WHERE title_key IS NOT NULL
ON CONFLICT (title_key, source) DO NOTHING;

DROP TABLE whitelist_entries;

-- 3. Add missing indexes for RSS 24h queries
CREATE INDEX IF NOT EXISTS idx_recent_chapters_created_at ON recent_chapters(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recent_chapters_title_key ON recent_chapters(title_key);
CREATE INDEX IF NOT EXISTS idx_dispatch_history_sent_at ON dispatch_history(sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_failed_dispatches_status ON failed_dispatches(status) WHERE status='pending';

-- 4. Fix dashboard_snapshot singleton integrity
ALTER TABLE dashboard_snapshot ALTER COLUMN computed_at SET NOT NULL;
ALTER TABLE dashboard_snapshot ADD CONSTRAINT dashboard_snapshot_id CHECK (id = 1);

-- 5. Fix manga_metadata PK → composite (title_key, source)
ALTER TABLE manga_metadata DROP CONSTRAINT IF EXISTS manga_metadata_pkey;
ALTER TABLE manga_metadata ADD PRIMARY KEY (title_key, source);
