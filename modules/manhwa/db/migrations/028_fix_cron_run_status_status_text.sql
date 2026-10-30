-- Migration 028: cron_run_status.status was changed to jsonb in 011 but code still inserts text 'ok'/'error'.
-- Fresh local DBs hit "invalid input syntax for type json Token ok is invalid" on write_cron_status.
-- Revert to text (original type) so inserts work; dashboard reads status == 'ok' as text.
ALTER TABLE cron_run_status ALTER COLUMN status TYPE text USING status::text;
