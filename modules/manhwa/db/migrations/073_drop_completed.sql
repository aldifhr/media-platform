-- 073_drop_completed.sql — revert 065_tamat_exclude: completed feature removed, exclude only

DROP INDEX IF EXISTS idx_excluded_titles_is_completed;
DROP INDEX IF EXISTS idx_excluded_titles_reason;

-- prefix cleanup before drop (in case any [COMPLETED] fallback rows exist)
UPDATE excluded_titles SET title = regexp_replace(title, '^\[COMPLETED\] ', '') WHERE title LIKE '[COMPLETED] %';

ALTER TABLE excluded_titles DROP COLUMN IF EXISTS is_completed;
ALTER TABLE excluded_titles DROP COLUMN IF EXISTS reason;
