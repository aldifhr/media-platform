-- Migration: Fix cron_run_status id NULL + add PK
-- Problem: 13,225 rows with id=NULL, no PK, no default
-- Fix: Backfill id with row_number(), add NOT NULL + DEFAULT + PK

-- Step 1: Backfill NULL ids with sequential numbers based on created_at
WITH numbered AS (
    SELECT ctid, row_number() OVER (ORDER BY created_at ASC, ctid ASC) as rn
    FROM cron_run_status
    WHERE id IS NULL
)
UPDATE cron_run_status SET id = numbered.rn
FROM numbered
WHERE cron_run_status.ctid = numbered.ctid;

-- Step 2: Add default sequence
CREATE SEQUENCE IF NOT EXISTS cron_run_status_id_seq;
ALTER TABLE cron_run_status ALTER COLUMN id SET DEFAULT nextval('cron_run_status_id_seq');
ALTER TABLE cron_run_status ALTER COLUMN id SET NOT NULL;

-- Step 3: Add primary key
ALTER TABLE cron_run_status ADD PRIMARY KEY (id);

-- Step 4: Create index on created_at for fast ORDER BY
CREATE INDEX IF NOT EXISTS idx_cron_run_status_created_at ON cron_run_status (created_at DESC);

-- Step 5: Sync sequence to max id
SELECT setval('cron_run_status_id_seq', COALESCE((SELECT MAX(id) FROM cron_run_status), 0) + 1, false);
