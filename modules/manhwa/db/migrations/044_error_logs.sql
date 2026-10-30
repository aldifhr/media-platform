-- error_logs: persistent backend error journal (30d TTL, admin view)
CREATE TABLE IF NOT EXISTS error_logs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    level TEXT NOT NULL CHECK (level IN ('debug','info','warn','error')),
    source TEXT NOT NULL DEFAULT 'app',
    message TEXT NOT NULL,
    stack TEXT,
    path TEXT,
    correlation_id TEXT,
    meta JSONB
);
CREATE INDEX IF NOT EXISTS idx_error_logs_created_at ON error_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_error_logs_level ON error_logs (level);
CREATE INDEX IF NOT EXISTS idx_error_logs_source ON error_logs (source);
-- retention helper (call from cron or manually): DELETE FROM error_logs WHERE created_at < NOW() - INTERVAL '30 days';
