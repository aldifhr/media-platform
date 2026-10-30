-- 076_prune_dispatch_history.sql
-- Dispatch history retention: 3 days max, auto-prune via cron daily

-- Index for prune performance
CREATE INDEX IF NOT EXISTS idx_dispatch_history_sent_at ON dispatch_history (sent_at);

-- One-off prune (> 3 days)
DELETE FROM dispatch_history WHERE sent_at < NOW() - INTERVAL '3 days';
