-- Migration 039 (2026-08-30): add metadata_enriched_at to whitelist
-- PERF-01: throttle upstream metadata enrichment. enrich_all_whitelist
-- now skips entries enriched within the refresh window (default 7d),
-- so we don't hammer source APIs (ikiru/shinigami) on every cron tick.
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS metadata_enriched_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_whitelist_metadata_enriched_at
    ON whitelist (metadata_enriched_at);
