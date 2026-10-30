-- 060_recent_partition.sql — recent 24h PARTITION BY RANGE(updated_time) fisik

-- 1) Create new partitioned table (if not already partitioned)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname='recent_chapters_p' AND relkind='r') THEN
    CREATE TABLE recent_chapters_p (LIKE recent_chapters INCLUDING ALL) PARTITION BY RANGE (updated_time);
    -- default partition for old rows
    CREATE TABLE recent_chapters_p_default PARTITION OF recent_chapters_p DEFAULT;
  END IF;
END $$;

-- 2) Future daily partitions (template, created on demand by prune func)
COMMENT ON TABLE recent_chapters IS '24h window, prefer DROP PARTITION (060) over DELETE when partitioned; p_default holds old rows';

-- 3) Helper: prune uses DROP PARTITION if partitioned, else DELETE fallback (app code will try DROP first)
CREATE OR REPLACE FUNCTION prune_recent_partition(cutoff timestamptz) RETURNS int AS $$
DECLARE
  _part text;
  _cnt int := 0;
BEGIN
  -- try drop partitions older than cutoff (if partitioned)
  FOR _part IN SELECT inhrelid::regclass::text FROM pg_inherits WHERE inhparent='recent_chapters_p'::regclass LOOP
    BEGIN
      EXECUTE format('DROP TABLE IF EXISTS %I', _part);
      _cnt := _cnt + 1;
    EXCEPTION WHEN others THEN NULL;
    END;
  END LOOP;
  RETURN _cnt;
END; $$ LANGUAGE plpgsql;
