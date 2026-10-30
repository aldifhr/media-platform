-- Migration 079: expression indexes backing the tolerant whitelist<->recent_chapters join.
-- The join predicate (app/core/sql_fragments.py, RC_WL_JOIN_ON) is:
--   w.source = rc.source AND (w.title_key = rc.title_key
--     OR replace(normalize_title_key(w.title_key),' ','-')
--        = replace(normalize_title_key(rc.title_key),' ','-'))
-- normalize_title_key(text) is IMMUTABLE (migration 045), so it may back an index.
-- Both sides get a normalized-dash expression index; the plain title_key btree
-- already exists on each table (idx_recent_chapters_title_key,
-- idx_whitelist_source_title_key) and covers the equality arm.

CREATE INDEX IF NOT EXISTS whitelist_title_key_norm_idx
    ON whitelist ((replace(normalize_title_key(title_key), ' ', '-')));

CREATE INDEX IF NOT EXISTS recent_chapters_title_key_norm_idx
    ON recent_chapters ((replace(normalize_title_key(title_key), ' ', '-')));
