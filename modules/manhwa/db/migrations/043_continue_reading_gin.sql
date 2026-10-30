-- Migration 043: GIN index for continue_reading.entries (analytics most_read)
-- Enables fast jsonb_object_keys scan for analytics_engagement without seq scan
CREATE INDEX IF NOT EXISTS idx_continue_reading_entries_gin ON public.continue_reading USING gin (entries jsonb_path_ops);
-- Also add index for to_timestamp(updated_at) range queries (24h/30d)
CREATE INDEX IF NOT EXISTS idx_continue_reading_updated_at ON public.continue_reading (updated_at DESC);
