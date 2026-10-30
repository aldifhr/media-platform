-- 042_db_audit_fix.sql � DB audit fix 1: recent_chapters composite UNIQUE
-- Enforces (title_key, source, chapter_num) uniqueness for numbered chapters.
-- Python dedup in recent_chapters.py is best-effort; DB is the race guard for
-- concurrent rss-fetch runs. One-shots (chapter_num = 0) are excluded � they
-- are never deduped and must allow multiple rows per title/source.
-- chapter_url unique remains (from 001_initial_schema / 014_fix_missing_unique_constraints).

-- Deduplicate existing rows first: keep newest id per composite key where numbered.
DELETE FROM recent_chapters
WHERE id IN (
  SELECT id FROM (
    SELECT id,
           ROW_NUMBER() OVER (
             PARTITION BY title_key, source, chapter_num
             ORDER BY id DESC
           ) AS rn
    FROM recent_chapters
    WHERE chapter_num <> 0
      AND chapter_num IS NOT NULL
  ) s
  WHERE rn > 1
);

-- Partial unique index for numbered chapters only. CREATE INDEX CONCURRENTLY
-- cannot run inside a transaction block, which broke the fresh-DB migration
-- chain (the runner wraps each file in a transaction) and skipped every
-- statement after it. Plain CREATE INDEX IF NOT EXISTS is idempotent and
-- transaction-safe; the table is small enough that the lock is irrelevant.
CREATE UNIQUE INDEX IF NOT EXISTS rc_composite ON recent_chapters(title_key, source, chapter_num) WHERE chapter_num <> 0;

-- Ensure chapter_url unique remains (idempotent).
CREATE UNIQUE INDEX IF NOT EXISTS recent_chapters_chapter_url_key ON recent_chapters (chapter_url);

-- ============================================================
-- DB audit fix 2: whitelist title_key canonical (slug)
-- Enforces title_key = slug (lowercase, [a-z0-9-]) so merge/delete
-- dedup is deterministic. Python already normalizes via
-- slugify_title_key (d89812b); DB is the race guard for
-- concurrent writes / direct inserts bypassing the model.
-- ============================================================

-- Backfill existing rows: space / mixed case / non-alnum -> slug
-- trim leading/trailing dashes so CHECK passes (regexp_replace alone leaves them)
UPDATE whitelist
SET title_key = trim(both '-' from lower(regexp_replace(title_key, '[^a-z0-9]+', '-', 'g')))
WHERE title_key !~ '^[a-z0-9-]+$' OR title_key <> lower(title_key);

-- Deduplicate rows that collided after slugification (keep newest)
DELETE FROM whitelist a USING whitelist b
WHERE a.title_key = b.title_key
  AND a.source = b.source
  AND a.ctid < b.ctid
  AND a.created_at < b.created_at;

-- Enforce slug format (idempotent)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_tk_slug') THEN
    ALTER TABLE whitelist ADD CONSTRAINT chk_tk_slug CHECK (title_key ~ '^[a-z0-9-]+$');
  END IF;
END $$;

-- Index for whitelist lookups by (source, title_key) — used by rss + dispatch
CREATE INDEX IF NOT EXISTS idx_whitelist_source_title_key ON whitelist (source, title_key);

-- ============================================================
-- DB audit fix 3: series_meta FK -> whitelist + index
-- series_meta is canonical single source for static fields (cover/rating/genres/description/type/origin);
-- whitelist is the parent (title_key, source). FK guarantees no orphan series_meta.
-- Deferrable so whitelist + series_meta can be inserted in either order within one txn.
-- ============================================================

-- Remove orphan series_meta rows that would block FK creation (whitelist is the subscription).
-- Guarded: series_meta is created by init script, may not exist on fresh DB when this migration runs.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='series_meta') THEN
    DELETE FROM series_meta sm
    WHERE NOT EXISTS (
      SELECT 1 FROM whitelist w
      WHERE w.title_key = sm.title_key AND w.source = sm.source
    );
  END IF;
END $$;

-- FK (deferrable); guarded so re-run is idempotent. Falls back to NOTICE if orphans remain.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='series_meta') THEN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_series_whitelist') THEN
      ALTER TABLE series_meta ADD CONSTRAINT fk_series_whitelist
        FOREIGN KEY (title_key, source) REFERENCES whitelist(title_key, source)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
    END IF;
  ELSE
    RAISE NOTICE 'series_meta missing, skip fk_series_whitelist';
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'fk_series_whitelist not added (orphans remain): %', SQLERRM;
END $$;

-- Index for FK lookups (PK already covers (title_key,source); explicit name helps EXPLAIN + future changes)
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='series_meta') THEN
    CREATE INDEX IF NOT EXISTS idx_series_meta_whitelist_fk ON series_meta (title_key, source);
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'idx_series_meta_whitelist_fk not added: %', SQLERRM;
END $$;

-- ============================================================
-- DB audit fix 4: dispatch_history unique + dispatch_claims transient
-- dispatch_history is the permanent notification record (id PK, FCFS
-- guard via fcfs_key + chapter_url uniques). Add (title_key, source,
-- chapter_title) unique so concurrent runners cannot double-notify
-- the same title+chapter via different URLs (shinigami/ikiru rotate
-- URLs each scrape; fcfs_key covers normalized title+chapter but
-- title_key+source+chapter_title is the business natural key used by
-- dispatch_mod legacy fallback and claim.py).
-- dispatch_claims is transient (short-TTL queue, FOR UPDATE SKIP LOCKED
-- in app/services/claim.py). It could be UNLOGGED or dropped entirely
-- once dispatch_history unique is trusted; minimal change here just
-- adds the permanent guard and documents the transient table — no DROP
-- yet to avoid breaking concurrent deploys / retention reaper.
-- ============================================================

-- Deduplicate existing dispatch_history rows on (title_key, source, chapter_title) keep newest id
DELETE FROM dispatch_history
WHERE id IN (
  SELECT id FROM (
    SELECT id,
           ROW_NUMBER() OVER (
             PARTITION BY title_key, source, chapter_title
             ORDER BY id DESC
           ) AS rn
    FROM dispatch_history
    WHERE title_key IS NOT NULL AND title_key <> ''
      AND chapter_title IS NOT NULL AND chapter_title <> ''
  ) s
  WHERE rn > 1
);

-- Permanent dedup guard: one row per title+source+chapter
CREATE UNIQUE INDEX IF NOT EXISTS dispatch_history_uq ON dispatch_history(title_key, source, chapter_title);

-- dispatch_claims transient note: short-TTL, SKIP LOCKED queue.
-- Could be UNLOGGED for less WAL (crash loses only transient claims):
--   ALTER TABLE dispatch_claims SET UNLOGGED;
-- Or dropped once callers rely solely on dispatch_history_uq + fcfs_key.
-- Not UNLOGGED/DROPPED here — minimal, document only.
COMMENT ON TABLE dispatch_claims IS 'transient claim queue (TTL ~1h, FOR UPDATE SKIP LOCKED in claim.py); could be UNLOGGED or dropped, dispatch_history is the permanent guard';
COMMENT ON INDEX dispatch_history_uq IS 'DB audit fix 4: prevents double-notify on (title_key, source, chapter_title)';

-- ============================================================
-- DB audit fix 5: dashboard_snapshot Redis-only (transient cache)
-- dashboard_snapshot is a singleton cache refreshed every cron run
-- (cron 60s, Redis TTL 600s, read TTL 300s). It needs no WAL
-- durability or PITR — cron recomputes it. Redis is now primary
-- (health.py writes Redis first, Supabase is backup). Make the
-- table UNLOGGED to avoid WAL bloat, or DROP it once Redis is
-- trusted. Minimal change: SET UNLOGGED + documenting comment.
-- ============================================================

-- UNLOGGED: crash loses only the transient cache (cron repopulates in <60s).
-- Guarded so re-run is idempotent; falls back to NOTICE if already UNLOGGED
-- or table missing on fresh DB.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='dashboard_snapshot') THEN
    PERFORM 1 FROM pg_class WHERE relname = 'dashboard_snapshot' AND relpersistence = 'u';
    IF NOT FOUND THEN
      ALTER TABLE dashboard_snapshot SET UNLOGGED;
    END IF;
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'dashboard_snapshot SET UNLOGGED skipped: %', SQLERRM;
END $$;

COMMENT ON TABLE dashboard_snapshot IS 'transient singleton cache (TTL 5m, cron refresh); Redis primary (health.py write/read prefer Redis, Supabase backup) — UNLOGGED to avoid WAL, can be DROPped once Redis trusted';

-- TTL helper already exists from 038; ensure it remains (idempotent).
CREATE INDEX IF NOT EXISTS idx_dashboard_snapshot_computed_at ON dashboard_snapshot (computed_at DESC);

-- ============================================================
-- DB audit fix 6: excluded_titles cover bloat
-- cover/series_url are static metadata canonical in series_meta
-- (title_key, source) — storing them in excluded_titles duplicates
-- data and requires presigned refresh (voratoon X-Amz). Prefer
-- JOIN series_meta.cover at read time (rss_service already does
-- sm > it > wl). Minimal fix: index on source to speed the
-- LIKE '%cvr.voratoon.id%X-Amz-%' scan which is always filtered
-- by source='voratoon', and document; do NOT DROP column yet
-- for back-compat (list_excluded_titles fast path).
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_excluded_titles_source ON excluded_titles(source);

-- Document bloat: cover should be JOINed, not stored here
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='excluded_titles' AND column_name='cover') THEN
    COMMENT ON COLUMN excluded_titles.cover IS 'bloat: prefer JOIN series_meta.cover on (title_key,source); kept for back-compat list_excluded_titles, refresh still source-filtered LIKE with idx_excluded_titles_source';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='excluded_titles' AND column_name='series_url') THEN
    COMMENT ON COLUMN excluded_titles.series_url IS 'bloat: prefer JOIN whitelist.series_url or series_meta; kept for back-compat';
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'fix6 comments skipped: %', SQLERRM;
END $$;

-- ============================================================
-- DB audit fix 7: missing composite indexes
-- ============================================================

-- rc_feed: recent_chapters feed query filtered by source/origin/type ordered by updated_time DESC
-- CONCURRENTLY cannot run in a transaction (see rc_composite note above).
CREATE INDEX IF NOT EXISTS rc_feed ON recent_chapters(updated_time DESC, source, origin, type) WHERE updated_time IS NOT NULL;

-- whitelist lookup by (source, title_key) — idempotent re-create (already from fix 2)
CREATE INDEX IF NOT EXISTS idx_whitelist_source_title_key ON whitelist(source, title_key);

-- dispatch_history time-range scans via BRIN (append-only, naturally ordered by sent_at/id)
CREATE INDEX IF NOT EXISTS dispatch_history_sent_at_brin ON dispatch_history USING BRIN (sent_at);
