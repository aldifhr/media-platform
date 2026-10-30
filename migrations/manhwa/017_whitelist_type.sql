-- Add `type` column to whitelist (manhua / manhwa / manga).
-- Source: shinigami "Format" taxonomy, ikiru "Type".
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS type text;

CREATE INDEX IF NOT EXISTS idx_whitelist_type ON whitelist (type);
