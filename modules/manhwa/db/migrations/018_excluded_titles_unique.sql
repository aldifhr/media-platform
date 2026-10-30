-- Migration 018: add missing UNIQUE(title_key, source) on excluded_titles
-- The live VPS Postgres drifted from the schema: excluded_titles had only a
-- PK on id (uuid) and NO unique constraint, so upsert(on_conflict="title_key,source")
-- failed with "there is no unique or exclusion constraint matching the ON CONFLICT
-- specification". This matches the same drift seen/fixed on whitelist (016) and
-- dispatch_claims/dispatch_history (017).
--
-- PostgreSQL does not support `ALTER TABLE ... ADD CONSTRAINT IF NOT EXISTS`,
-- so guard with a DO block.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'excluded_titles'::regclass
      AND contype = 'u'
      AND pg_get_constraintdef(oid) ILIKE '%(title_key, source)%'
  ) THEN
    ALTER TABLE excluded_titles
      ADD CONSTRAINT excluded_titles_title_key_source_uniq UNIQUE (title_key, source);
  END IF;
END $$;
