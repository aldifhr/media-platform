-- 065_tamat_exclude.sql — tamat flag di /recent + exclude dari RSS

-- Recent items belom tentu di whitelist, jadi simpan di excluded_titles (filter existing) + reason=completed buat badge TAMAT

-- reason: 'excluded' (manual hide) vs 'completed' (tamat)
ALTER TABLE excluded_titles ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT 'excluded';
ALTER TABLE excluded_titles ADD COLUMN IF NOT EXISTS is_completed BOOLEAN NOT NULL DEFAULT FALSE;

-- backfill existing rows: reason stays 'excluded'
-- index for badge/filter (few completed rows)
CREATE INDEX IF NOT EXISTS idx_excluded_titles_reason ON excluded_titles(reason);
CREATE INDEX IF NOT EXISTS idx_excluded_titles_is_completed ON excluded_titles(is_completed) WHERE is_completed = TRUE;

COMMENT ON COLUMN excluded_titles.reason IS 'excluded vs completed (tamat) — both filtered from RSS, completed shows TAMAT badge';
COMMENT ON COLUMN excluded_titles.is_completed IS 'true = tamat (completed), filtered from RSS + badge TAMAT di /recent';
