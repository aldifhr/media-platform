-- 014_fix_missing_unique_constraints.sql
-- ROOT CAUSE: live DB (be_ag_py @ 127.0.0.1:5432) was provisioned WITHOUT the
-- unique/PK constraints that app code relies on for `.upsert(on_conflict=...)`.
-- Tables: recent_chapters, manga_metadata, source_health, dashboard_snapshot.
-- Every cron upsert was failing with:
--   "there is no unique or exclusion constraint matching the ON CONFLICT specification"
-- so 0 rows were persisted (RSS feed stale, metadata/health/snapshot never updated).
--
-- This migration adds the missing constraints (idempotent DO-block guards) and
-- backfills any NULL keys so the PK/unique ADD does not fail on duplicates/NULLs.
-- Run with: psql or via the app venv `python -c` against DATABASE_URL.
-- Safe to re-run: every statement is guarded.

begin;

-- ============================================================
-- 1. recent_chapters: unique(chapter_url)
-- ============================================================
-- Backfill any NULL/empty chapter_url (not-null violation would block the index).
do $$
begin
  -- mark dup rows: keep lowest id, null out the rest so the unique index can be added
  if not exists (
    select 1 from pg_constraint c
    join pg_class rel on rel.oid = c.conrelid
    where rel.relname = 'recent_chapters' and c.contype = 'u'
      and pg_get_constraintdef(c.oid) like '%chapter_url%'
  ) then
    -- ensure no NULL/empty chapter_url remains (would violate unique index)
    update recent_chapters set chapter_url = 'dup_' || id::text
    where chapter_url is null or chapter_url = '';
    -- drop exact duplicate chapter_url keeping the lowest id
    delete from recent_chapters a
    using recent_chapters b
    where a.chapter_url = b.chapter_url
      and a.id > b.id;
    alter table recent_chapters
      add constraint recent_chapters_chapter_url_key unique (chapter_url);
  end if;
end $$;

-- ============================================================
-- 2. manga_metadata: primary key (title_key)
-- ============================================================
do $$
begin
  if not exists (
    select 1 from pg_constraint c
    join pg_class rel on rel.oid = c.conrelid
    where rel.relname = 'manga_metadata' and c.contype = 'p'
  ) then
    -- dedupe: keep first title_key, drop the rest
    delete from manga_metadata a
    using manga_metadata b
    where a.title_key = b.title_key
      and a.ctid > b.ctid;
    -- backfill NULL title_key (cannot be PK)
    update manga_metadata set title_key = 'unknown_' || ctid::text
    where title_key is null or title_key = '';
    alter table manga_metadata add primary key (title_key);
  end if;
end $$;

-- ============================================================
-- 3. source_health: primary key (source)
-- ============================================================
do $$
begin
  if not exists (
    select 1 from pg_constraint c
    join pg_class rel on rel.oid = c.conrelid
    where rel.relname = 'source_health' and c.contype = 'p'
  ) then
    delete from source_health a
    using source_health b
    where a.source = b.source
      and a.ctid > b.ctid;
    update source_health set source = 'unknown_' || ctid::text
    where source is null or source = '';
    alter table source_health add primary key (source);
  end if;
end $$;

-- ============================================================
-- 4. dashboard_snapshot: primary key (id) singleton
-- ============================================================
do $$
begin
  if not exists (
    select 1 from pg_constraint c
    join pg_class rel on rel.oid = c.conrelid
    where rel.relname = 'dashboard_snapshot' and c.contype = 'p'
  ) then
    -- keep only the first row, drop extras (singleton table)
    delete from dashboard_snapshot
    where id <> (select min(id) from dashboard_snapshot);
    alter table dashboard_snapshot add primary key (id);
  end if;
end $$;

commit;
