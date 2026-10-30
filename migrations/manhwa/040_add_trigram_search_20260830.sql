-- Add trigram search support so catalog title search (ILIKE '%term%')
-- doesn't degrade into a full sequential scan as the whitelist table grows.
-- pg_trgm ships with Supabase Postgres; CREATE EXTENSION is a one-time,
-- idempotent op. The GIN index accelerates both leading- and trailing-wildcard
-- ILIKE, which a plain B-tree cannot serve.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- whitelist.title is the column catalog_list filters on (ILIKE '%s%').
CREATE INDEX IF NOT EXISTS idx_whitelist_title_trgm
    ON whitelist USING gin (title gin_trgm_ops);

-- recent_chapters.title_key is also ILIKE-filtered in some catalog paths.
CREATE INDEX IF NOT EXISTS idx_recent_chapters_title_key_trgm
    ON recent_chapters USING gin (title_key gin_trgm_ops);
