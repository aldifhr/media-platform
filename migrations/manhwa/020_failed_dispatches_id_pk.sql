-- Migration 020: failed_dispatches.id must be a real PK (live DB drifted:
-- the column existed as uuid NULLABLE with NO default and NO primary key,
-- so inserts left id=NULL and retry_failed_dispatches() could never locate
-- the row to flip status->'resolved' (update eq("id", None) matched nothing).
-- Same drift class as whitelist(016)/dispatch(017)/excluded_titles(018).

-- 1) Give id a default so new rows always get a uuid.
ALTER TABLE failed_dispatches
  ALTER COLUMN id SET DEFAULT gen_random_uuid();

-- 2) Backfill any existing NULL ids (shouldn't be many; safe either way).
UPDATE failed_dispatches SET id = gen_random_uuid() WHERE id IS NULL;

-- 3) Promote to primary key.
ALTER TABLE failed_dispatches ADD PRIMARY KEY (id);
