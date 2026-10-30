-- 072_index_cleanup_v2.sql
-- Drop unused trgm + BRIN, add missing single-col indexes

-- Drop: unused trgm index (592 KB, 0 scans)
DROP INDEX IF EXISTS idx_whitelist_title_trgm;

-- Drop: unused BRIN index (24 KB, 0 scans — BRIN overhead not worth it for 224 rows)
DROP INDEX IF EXISTS dispatch_history_sent_at_brin;

-- Add: whitelist title_key lookups (ILIKE '%solo%' searches)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_whitelist_title_key ON whitelist (title_key);

-- Add: dispatch_history title_key lookups (legacy fallback)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dispatch_history_title_key ON dispatch_history (title_key);
