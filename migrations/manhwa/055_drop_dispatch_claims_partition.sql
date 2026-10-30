-- 055_drop_dispatch_claims_partition.sql — 9.0 final

-- 1) dispatch_claims is transient (expires_at < now() + 1h), covered by dispatch_history_uq UNIQUE; drop to remove double write
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='dispatch_claims') THEN
    DROP TABLE dispatch_claims;
  END IF;
END $$;

-- 2) recent_chapters 24h window't actually PARTITION yet (Supabase pooler + PostgREST partition pruning needs app change)
-- Keep comment + index for future: rc_feed already covers time filter, PARTITION add when row count >100k or prune >1s measured
COMMENT ON TABLE recent_chapters IS '24h window, prune_older_than DELETE lt cutoff (055) — PARTITION BY RANGE (updated_time) add when >100k rows';
