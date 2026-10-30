-- 047_sql_tune.sql
-- Debt kecil SQL — zero downtime, CREATE INDEX CONCURRENTLY

-- 1. ILIKE '%q%' di rss.py & error_logs tanpa trigram → seq scan
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_recent_chapters_title_trgm
  ON recent_chapters USING gin (title gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_error_logs_message_trgm
  ON error_logs USING gin (message gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_error_logs_source_trgm
  ON error_logs USING gin (source gin_trgm_ops);

-- 2. Bookmark join REPLACE(LOWER(title_key)) & recent_chapters lateral
--    plus dispatch_history sent_at filter
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_recent_chapters_titlekey_source
  ON recent_chapters (title_key, source);
CREATE INDEX IF NOT EXISTS idx_dispatch_history_titlekey_source_sent
  ON dispatch_history (title_key, source, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_chapter_bookmarks_source
  ON chapter_bookmarks (source);
CREATE INDEX IF NOT EXISTS idx_chapter_bookmarks_titlekey_source
  ON chapter_bookmarks (title_key, source);

-- 3. Expression index untuk bookmark join & gap_detector REPLACE normalize
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_recent_chapters_normalized
  ON recent_chapters (normalize_title_key(title_key), source, updated_time DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_whitelist_normalized
  ON whitelist (normalize_title_key(title_key), source);

-- 4. recent_chapters prune & rss window
CREATE INDEX IF NOT EXISTS idx_recent_chapters_updated_time
  ON recent_chapters (updated_time DESC) WHERE updated_time IS NOT NULL;
