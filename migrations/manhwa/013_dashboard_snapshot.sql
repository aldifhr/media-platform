-- 013_dashboard_snapshot.sql
-- Materialized dashboard snapshot: cron computes the expensive
-- dashboard payload once per run and writes 1 row. The
-- /api/dashboard-snapshot endpoint then reads 1 row
-- (20ms) instead of recomputing 5 parallel Supabase queries
-- (~3s). Event-driven refresh: only cron writes, FE only reads.

create table if not exists public.dashboard_snapshot (
  id          bigint      primary key default 1,  -- singleton row (id=1)
  payload     jsonb       not null,
  computed_at timestamptz not null default now()
);

-- Ensure only 1 row ever exists (singleton).
create unique index if not exists uq_dashboard_snapshot_singleton
  on public.dashboard_snapshot (id);
