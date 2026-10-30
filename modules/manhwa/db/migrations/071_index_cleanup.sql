-- 071_index_cleanup.sql
-- Drop duplicate/unused indexes, add composite indexes

-- ═══════════════════════════════════════════════════════════
-- 1. DROPS (zero risk, confirmed redundant/unused)
-- ═══════════════════════════════════════════════════════════

DROP INDEX IF EXISTS idx_recent_chapters_titlekey_source;   -- dup of idx_recent_chapters_title_key_source
DROP INDEX IF EXISTS idx_recent_chapters_updated_time;      -- dup of idx_recent_chapters_updated_time_desc
DROP INDEX IF EXISTS idx_error_logs_message_trgm;           -- 5.9 MB, 2 scans, never used
DROP INDEX IF EXISTS idx_error_logs_source_trgm;            -- 1.9 MB, 1 scan, never used
DROP INDEX IF EXISTS idx_test;                              -- test leftover
DROP INDEX IF EXISTS idx_dh_title_key_chapter;              -- prefix of dispatch_history_uq
DROP INDEX IF EXISTS idx_chapter_bookmarks_session_hash;    -- dup of idx_chapter_bookmarks_session
DROP INDEX IF EXISTS idx_continue_reading_updated_at_tz;    -- dup of idx_continue_reading_updated_at

-- ═══════════════════════════════════════════════════════════
-- 2. NEW INDEXES (cover critical queries as data grows)
-- ═══════════════════════════════════════════════════════════

-- recent_chapters 24h fetch (Seq Scan → Index Scan as data grows)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rc_24h_updated
    ON recent_chapters (updated_time DESC, id DESC);

-- trending aggregation (cover GROUP BY title_key, source + updated_time)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rc_trending
    ON recent_chapters (title_key, source, updated_time DESC);
