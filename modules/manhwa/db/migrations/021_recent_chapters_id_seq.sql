-- Migration 021: recent_chapters.id must be a populated, ordered key.
-- Live DB drifted: the `id` column was bigint NULLABLE with NO default
-- (no sequence), so every inserted row got id=NULL. RSS pagination ordered
-- by id DESC then collapsed to all-NULL ordering -> unstable page windows
-- (page 1 and page 2 overlapped). Fix: attach a sequence default so new
-- rows get a monotonic id, and backfill existing NULLs.
--
-- NOTE: recent_chapters is keyed by chapter_url (PK-equivalent via upsert
-- on_conflict="chapter_url"), so id is only used for stable ordering, not
-- as a join key. A sequence default is sufficient; no PK needed.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_sequences WHERE schemaname='public' AND sequencename='recent_chapters_id_seq') THEN
    CREATE SEQUENCE recent_chapters_id_seq;
  END IF;
END $$;

ALTER TABLE recent_chapters ALTER COLUMN id SET DEFAULT nextval('recent_chapters_id_seq');

-- Backfill NULL ids using the sequence (preserves insertion-ish order via ctid).
UPDATE recent_chapters SET id = nextval('recent_chapters_id_seq') WHERE id IS NULL;

-- Make the column NOT NULL now that every row has a value.
ALTER TABLE recent_chapters ALTER COLUMN id SET NOT NULL;
