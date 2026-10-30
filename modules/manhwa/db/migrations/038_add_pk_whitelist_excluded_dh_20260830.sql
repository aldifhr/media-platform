-- Migration 038 (2026-08-30): Add explicit PKs to whitelist, excluded_titles,
-- and dispatch_history. REPORT.md §6 rec 1-3. These tables only had UNIQUE
-- constraints (de-facto PK) but no real PRIMARY KEY / id column, which makes
-- row-level upserts, explicit ordering, and future schema changes fragile.
-- Low risk: whitelist (63 rows), excluded_titles (0 rows), dispatch_history (218).

-- 1) whitelist: add id UUID PK
ALTER TABLE whitelist ADD COLUMN IF NOT EXISTS id uuid DEFAULT gen_random_uuid();
UPDATE whitelist SET id = gen_random_uuid() WHERE id IS NULL;
ALTER TABLE whitelist ALTER COLUMN id SET NOT NULL;
ALTER TABLE whitelist ADD PRIMARY KEY (id);

-- 2) excluded_titles: add id UUID PK (few rows, safe)
ALTER TABLE excluded_titles ADD COLUMN IF NOT EXISTS id uuid DEFAULT gen_random_uuid();
UPDATE excluded_titles SET id = gen_random_uuid() WHERE id IS NULL;
ALTER TABLE excluded_titles ALTER COLUMN id SET NOT NULL;
ALTER TABLE excluded_titles ADD PRIMARY KEY (id);

-- 3) dispatch_history: add id BIGSERIAL PK (REPORT rec 3: ordering by insert time)
--    Migration 011 intentionally dropped id; we re-add it now that the table
--    is stable and explicit PKs are wanted. chapter_url UNIQUE stays as the
--    natural business key; id is the surrogate PK for row addressing.
ALTER TABLE dispatch_history ADD COLUMN IF NOT EXISTS id bigserial;
-- Backfill existing rows with sequential ids
WITH numbered AS (
    SELECT ctid, row_number() OVER (ORDER BY created_at ASC, ctid ASC) AS rn
    FROM dispatch_history WHERE id IS NULL
)
UPDATE dispatch_history SET id = numbered.rn
FROM numbered WHERE dispatch_history.ctid = numbered.ctid;
ALTER TABLE dispatch_history ALTER COLUMN id SET NOT NULL;
ALTER TABLE dispatch_history ADD PRIMARY KEY (id);
CREATE INDEX IF NOT EXISTS idx_dispatch_history_sent_at ON dispatch_history (sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_dispatch_history_fcfs_key ON dispatch_history (fcfs_key);

-- 4) dashboard_snapshot: TTL enforcement (REPORT rec 5).
--    The snapshot is a single-row cache (id=1) refreshed on every cron run.
--    Add a hard retention so a stale snapshot (cron down) is detectable: the
--    reader treats any snapshot older than 5 min as stale and falls back to
--    live DB queries. We add a partial index helper + ensure computed_at index.
CREATE INDEX IF NOT EXISTS idx_dashboard_snapshot_computed_at
    ON dashboard_snapshot (computed_at DESC);
