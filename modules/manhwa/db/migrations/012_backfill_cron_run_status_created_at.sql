-- Backfill legacy NULL created_at rows in cron_run_status,
-- then enforce NOT NULL + default now() so new rows never regress.
-- Safe to run once on a live DB.

UPDATE cron_run_status
SET created_at = COALESCE(created_at, now())
WHERE created_at IS NULL;

ALTER TABLE cron_run_status
  ALTER COLUMN created_at SET DEFAULT now(),
  ALTER COLUMN created_at SET NOT NULL;
