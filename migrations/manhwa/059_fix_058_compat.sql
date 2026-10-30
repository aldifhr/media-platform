-- 059_fix_058_compat.sql — undo 058 drop until code fully migrated to series_meta

ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS cover TEXT;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS rating FLOAT;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS genres JSONB;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS type TEXT;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS origin TEXT;
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS status TEXT;
