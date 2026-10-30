-- Backfill NULL created_at in whitelist from dispatch_history earliest sent_at,
-- falling back to now() for rows with no dispatch history. Then enforce NOT NULL
-- and default now() so future inserts always populate it.
DO $$
DECLARE updated_rows INT := 0;
BEGIN
  UPDATE whitelist w
  SET created_at = COALESCE(
      (SELECT min(dh.sent_at)
       FROM dispatch_history dh
       WHERE dh.title_key = w.title_key AND dh.source = w.source),
      now()
  )
  WHERE w.created_at IS NULL;

  GET DIAGNOSTICS updated_rows = ROW_COUNT;
  RAISE NOTICE 'whitelist created_at backfilled rows=%', updated_rows;
END $$;

ALTER TABLE whitelist
  ALTER COLUMN created_at SET DEFAULT now(),
  ALTER COLUMN created_at SET NOT NULL;
