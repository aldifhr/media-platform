-- 067: re-add ikiru to source CHECKs, allow permanent_failure, unique
-- failed_dispatches.chapter_url, and backstop the 051 statements that
-- CONCURRENTLY skipped in a transaction.
--
-- 053 added komiku but dropped ikiru from the recent_chapters / whitelist /
-- source_health CHECK constraints; 066 only restored it on excluded_titles.
-- Any ikiru row therefore still fails the constraint.
--
-- record_failed() upserts failed_dispatches ON CONFLICT (chapter_url), which
-- requires a unique index; the table only had a PK on id, so the upsert threw.
-- retry_failed_dispatches() also writes status='permanent_failure', which the
-- migration-011 CHECK (failed/retrying/resolved) rejected.
--
-- EVERY statement is wrapped so one failure can never abort the migration and
-- block API startup (the runner wraps each file in a transaction).

-- 1. Re-add ikiru (keep komiku) to the source CHECK constraints.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_recent_chapters_source') THEN
    ALTER TABLE recent_chapters DROP CONSTRAINT chk_recent_chapters_source;
  END IF;
  ALTER TABLE recent_chapters ADD CONSTRAINT chk_recent_chapters_source
    CHECK (source IN ('shinigami', 'komiku', 'voratoon', 'ikiru', ''));
EXCEPTION WHEN others THEN
  RAISE NOTICE 'chk_recent_chapters_source not updated: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_whitelist_source') THEN
    ALTER TABLE whitelist DROP CONSTRAINT chk_whitelist_source;
  END IF;
  ALTER TABLE whitelist ADD CONSTRAINT chk_whitelist_source
    CHECK (source IN ('shinigami', 'komiku', 'voratoon', 'ikiru', ''));
EXCEPTION WHEN others THEN
  RAISE NOTICE 'chk_whitelist_source not updated: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_source_health_source') THEN
    ALTER TABLE source_health DROP CONSTRAINT chk_source_health_source;
  END IF;
  ALTER TABLE source_health ADD CONSTRAINT chk_source_health_source
    CHECK (source IN ('shinigami', 'komiku', 'voratoon', 'ikiru'));
EXCEPTION WHEN others THEN
  RAISE NOTICE 'chk_source_health_source not updated: %', SQLERRM;
END $$;

-- 2. Allow status='permanent_failure' (retry writes it; 011 CHECK rejected it).
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'failed_dispatches_status_check') THEN
    ALTER TABLE failed_dispatches DROP CONSTRAINT failed_dispatches_status_check;
  END IF;
  ALTER TABLE failed_dispatches ADD CONSTRAINT failed_dispatches_status_check
    CHECK (status IN ('failed', 'retrying', 'resolved', 'permanent_failure'));
EXCEPTION WHEN others THEN
  RAISE NOTICE 'failed_dispatches_status_check not updated: %', SQLERRM;
END $$;

-- 3. failed_dispatches.chapter_url unique (required by record_failed upsert).
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
  RAISE NOTICE 'uidx_failed_dispatches_chapter_url not created: %', SQLERRM;
END $$;

-- 4. Backstop the 051 statements that never ran on a fresh DB because
--    CREATE INDEX CONCURRENTLY aborted the surrounding transaction. Dedupe
--    first so the unique index cannot fail on pre-existing duplicates.
DO $$ BEGIN
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
EXCEPTION WHEN others THEN
  RAISE NOTICE 'recent_chapters dedupe skipped: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'rc_composite') THEN
    CREATE UNIQUE INDEX rc_composite
      ON recent_chapters(title_key, source, chapter_num) WHERE chapter_num <> 0;
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'rc_composite not created: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'rc_feed') THEN
    CREATE INDEX rc_feed
      ON recent_chapters(updated_time DESC, source, origin, type) WHERE updated_time IS NOT NULL;
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'rc_feed not created: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_tk_slug') THEN
    ALTER TABLE whitelist ADD CONSTRAINT chk_tk_slug CHECK (title_key ~ '^[a-z0-9-]+$');
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'chk_tk_slug not added: %', SQLERRM;
END $$;

DO $$ BEGIN
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
EXCEPTION WHEN others THEN
  RAISE NOTICE 'dispatch_history dedupe skipped: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'dispatch_history_uq') THEN
    CREATE UNIQUE INDEX dispatch_history_uq
      ON dispatch_history(title_key, source, chapter_title);
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'dispatch_history_uq not created: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_whitelist_source_title_key') THEN
    CREATE INDEX idx_whitelist_source_title_key ON whitelist(source, title_key);
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'idx_whitelist_source_title_key not created: %', SQLERRM;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_series_meta_whitelist_fk') THEN
    CREATE INDEX idx_series_meta_whitelist_fk ON series_meta(title_key, source);
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'idx_series_meta_whitelist_fk not created: %', SQLERRM;
END $$;
