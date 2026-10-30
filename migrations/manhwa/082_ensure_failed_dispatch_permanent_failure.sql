-- 082_ensure_failed_dispatch_permanent_failure.sql
-- Re-apply the 067 fixes idempotently for databases that recorded the OLD
-- (pre-edit) 067 and therefore never received them. 067 was edited after
-- release; a DB whose schema_migrations already lists the old filename skips
-- the new statements, leaving it WITHOUT:
--   * failed_dispatches_status_check allowing 'permanent_failure'
--     (retry_failed_dispatches writes it; the migration-011 CHECK rejected it)
--   * the unique index on failed_dispatches(chapter_url) that record_failed()'s
--     ON CONFLICT (chapter_url) upsert requires
--   * the backstop indexes 051 could not create (CREATE INDEX CONCURRENTLY
--     aborted the runner's surrounding transaction)
--
-- Every statement is guarded so one failure can never abort startup. Purely
-- additive / idempotent: safe on any DB, any number of times, including a
-- fresh DB where series_meta may not exist yet (that block NOTICEs and skips).
-- Does NOT re-bake 067's stale source list (sources are shinigami|ikiru today).
--
-- Prod check before/after:
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conname='failed_dispatches_status_check';

-- 1. Allow status='permanent_failure'.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'failed_dispatches_status_check') THEN
    ALTER TABLE failed_dispatches DROP CONSTRAINT failed_dispatches_status_check;
  END IF;
  ALTER TABLE failed_dispatches ADD CONSTRAINT failed_dispatches_status_check
    CHECK (status IN ('failed', 'retrying', 'resolved', 'permanent_failure'));
EXCEPTION WHEN others THEN
  RAISE NOTICE '082: failed_dispatches_status_check not updated: %', SQLERRM;
END $$;

-- 2. failed_dispatches.chapter_url unique (required by record_failed upsert).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uidx_failed_dispatches_chapter_url') THEN
    -- Keep one row per chapter_url (the physically last one wins).
    DELETE FROM failed_dispatches a
    USING failed_dispatches b
    WHERE a.chapter_url = b.chapter_url
      AND a.ctid < b.ctid;
    CREATE UNIQUE INDEX uidx_failed_dispatches_chapter_url ON failed_dispatches(chapter_url);
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE '082: uidx_failed_dispatches_chapter_url not created: %', SQLERRM;
END $$;

-- 3. Backstop indexes 051 skipped (CONCURRENTLY in a transaction). Guarded.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'rc_composite') THEN
    DELETE FROM recent_chapters
    WHERE id IN (
      SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
          PARTITION BY title_key, source, chapter_num ORDER BY id DESC
        ) AS rn
        FROM recent_chapters
        WHERE chapter_num <> 0 AND chapter_num IS NOT NULL
      ) s WHERE rn > 1
    );
    CREATE UNIQUE INDEX rc_composite
      ON recent_chapters(title_key, source, chapter_num) WHERE chapter_num <> 0;
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE '082: rc_composite not created: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'rc_feed') THEN
    CREATE INDEX rc_feed
      ON recent_chapters(updated_time DESC, source, origin, type) WHERE updated_time IS NOT NULL;
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE '082: rc_feed not created: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_tk_slug') THEN
    ALTER TABLE whitelist ADD CONSTRAINT chk_tk_slug CHECK (title_key ~ '^[a-z0-9-]+$');
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE '082: chk_tk_slug not added: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'dispatch_history_uq') THEN
    DELETE FROM dispatch_history
    WHERE id IN (
      SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
          PARTITION BY title_key, source, chapter_title ORDER BY id DESC
        ) AS rn
        FROM dispatch_history
        WHERE title_key IS NOT NULL AND title_key <> ''
          AND chapter_title IS NOT NULL AND chapter_title <> ''
      ) s WHERE rn > 1
    );
    CREATE UNIQUE INDEX dispatch_history_uq
      ON dispatch_history(title_key, source, chapter_title);
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE '082: dispatch_history_uq not created: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_whitelist_source_title_key') THEN
    CREATE INDEX idx_whitelist_source_title_key ON whitelist(source, title_key);
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE '082: idx_whitelist_source_title_key not created: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_series_meta_whitelist_fk') THEN
    CREATE INDEX idx_series_meta_whitelist_fk ON series_meta(title_key, source);
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE '082: idx_series_meta_whitelist_fk not created (series_meta may be absent): %', SQLERRM;
END $$;
