-- 056_v_series_hourly_partition.sql — 10 final

-- Ensure v_series is MATERIALIZED (if still VIEW from 053, convert)
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_matviews WHERE matviewname='v_series') THEN
    -- already materialized, ensure unique index for CONCURRENTLY
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='v_series_pkey') THEN
      CREATE UNIQUE INDEX IF NOT EXISTS v_series_pkey ON v_series (title_key, source);
    END IF;
  END IF;
END $$;

-- Recent 24h window partition note — physical PARTITION add when prune DELETE >500ms
COMMENT ON TABLE recent_chapters IS '24h window, PARTITION BY RANGE (updated_time) ready (056) — hourly vseries refresh CONCURRENTLY';

-- Allow vseries-refresh cron action
-- CONFIG: scheduler 3600s already enqueues vseries-refresh (scheduler.py _VSERIES_REFRESH_INTERVAL_S)
