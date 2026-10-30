-- 002_add_chapter_num.sql
-- Incremental migration for existing deployments.
-- Jalanin setelah 001_initial_schema.sql.

-- ============================================================
-- recent_chapters: add column if not exists
-- ============================================================
alter table recent_chapters add column if not exists chapter_num numeric;

-- ============================================================
-- dispatch_history: ensure source column exists and fix sent_at
-- ============================================================
alter table dispatch_history add column if not exists source text;
alter table dispatch_history add column if not exists created_at timestamptz not null default now();

-- set default for sent_at if not already set
-- (existing rows with literal 'now()' string will stay, new rows get proper timestamp)
do $$
begin
  if not exists (
    select 1 from information_schema.columns
    where table_name = 'dispatch_history' and column_name = 'sent_at'
      and column_default is not null
  ) then
    alter table dispatch_history alter column sent_at set default now();
  end if;
end $$;

-- ============================================================
-- source_health: ensure defaults match schema
-- ============================================================
alter table source_health alter column consecutive_failures set default 0;
alter table source_health alter column failures_today set default 0;
alter table source_health alter column successes_today set default 0;
