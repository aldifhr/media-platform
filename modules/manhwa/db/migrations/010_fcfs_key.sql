ALTER TABLE dispatch_history ADD COLUMN IF NOT EXISTS fcfs_key text;

CREATE INDEX IF NOT EXISTS idx_dispatch_history_fcfs_key
  ON dispatch_history (fcfs_key);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'dispatch_history' AND column_name = 'chapter_title'
  ) THEN
    UPDATE dispatch_history
    SET fcfs_key = lower(regexp_replace(
        coalesce(chapter_title, '') || '#' || coalesce(title_key, ''),
        '[^a-z0-9#]', ' ', 'g'))
    WHERE fcfs_key IS NULL;
  END IF;
END $$;
