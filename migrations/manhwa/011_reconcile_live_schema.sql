-- 011_reconcile_live_schema.sql
-- Reconciliation migration: brings the repo SQL in line with the LIVE Supabase
-- schema so the DB is reproducible from migrations and the RPCs match reality.
--
-- Design rules:
--   * Every statement is idempotent (IF NOT EXISTS / DO-block guards) so it is
--     safe to re-run on the already-correct live DB AND on a fresh DB.
--   * No destructive drops of data-bearing objects without a guard.
--   * Run order: extensions -> missing tables -> column/PK/type fixes ->
--     indexes -> RPC rewrites.
--
-- NOTE: sql/failed_dispatches.sql is now STALE (outside this chain and
-- out of sync with live). The canonical failed_dispatches DDL lives below.
-- Delete that file after this migration is applied.
--
-- NOTE: whitelist_entries + cron_locks (present in earlier drafts) were DROPPED
-- from this migration: grep of app/ shows ZERO references to either table
-- (cron uses a file lock, whitelist uses add_whitelist_entries python method,
-- not a whitelist_entries SQL table). Creating them would be cruft.

-- ============================================================
-- 0. Extensions
-- ============================================================
create extension if not exists pgcrypto;

-- ============================================================
-- 1. Missing tables (exist in live, absent from migrations)
--    All CREATE TABLE IF NOT EXISTS so re-running is a no-op on live.
-- ============================================================

create table if not exists public.dispatch_claims (
  chapter_url   text        not null,
  title_key     text        not null,
  status        text        not null default 'pending'
                  check (status = any (array['pending','sent','expired'])),
  duplicate_key text,
  claimed_at    timestamptz not null default now(),
  sent_at       timestamptz,
  expires_at    timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint dispatch_claims_pkey primary key (chapter_url)
);

create table if not exists public.user_follows (
  user_id    text        not null,
  title_key  text        not null,
  created_at timestamptz not null default now(),
  constraint user_follows_pkey primary key (user_id, title_key)
);

create table if not exists public.cron_logs (
  id           bigint generated always as identity primary key,
  timestamp    timestamptz not null default now(),
  tag          text,
  code         text,
  type         text,
  source       text,
  title        text,
  count        integer     default 0,
  sent         integer     default 0,
  skipped      integer     default 0,
  failed_count integer     default 0,
  message      text,
  raw_payload  jsonb,
  created_at   timestamptz not null default now()
);

create table if not exists public.scraper_stats (
  date               text    not null,
  sent               integer default 0,
  skipped            integer default 0,
  failed             integer default 0,
  hibernated         integer default 0,
  incremental_saved  integer default 0,
  guilds             integer default 0,
  scraped            integer default 0,
  duration_avg       double precision default 0,
  raw_data           jsonb,
  updated_at         timestamptz not null default now(),
  constraint scraper_stats_pkey primary key (date)
);

create table if not exists public.live_events (
  id         bigint generated always as identity primary key,
  timestamp  timestamptz not null default now(),
  message    text        not null,
  type       text        not null default 'info'
);

create table if not exists public.scrape_history (
  title_key     text        not null,
  last_check_at timestamptz not null default now(),
  created_at    timestamptz not null default now(),
  constraint scrape_history_pkey primary key (title_key)
);

create table if not exists public.cron_locks (
  name        text        not null,
  instance_id text        not null,
  expires_at  timestamptz not null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  constraint cron_locks_pkey primary key (name)
);
create table if not exists public.title_last_chapters (
  title_key       text        not null,
  chapter_number  double precision not null default 0,
  updated_at      timestamptz not null default now(),
  created_at      timestamptz not null default now(),
  constraint title_last_chapters_pkey primary key (title_key)
);

create table if not exists public.channel_validation_cache (
  channel_id  text        not null,
  valid       boolean     not null,
  expires_at  timestamptz not null,
  updated_at  timestamptz not null default now(),
  created_at  timestamptz not null default now(),
  constraint channel_validation_cache_pkey primary key (channel_id)
);

create table if not exists public.read_progress (
  user_id       text        not null,
  title_key     text        not null,
  chapter_url   text        not null,
  chapter_title text,
  read_at       timestamptz not null default now(),
  constraint read_progress_pkey primary key (user_id, title_key)
);

create table if not exists public.app_settings (
  key        text        not null,
  value      text,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  constraint app_settings_pkey primary key (key)
);

-- failed_dispatches: canonical live DDL (supersedes sql/failed_dispatches.sql)
create table if not exists public.failed_dispatches (
  id             uuid        not null default gen_random_uuid(),
  chapter_url    text        not null,
  title_key      text        not null default '',
  chapter_title  text        not null default '',
  chapter_number numeric,
  source         text        not null default '',
  channel_id     text,
  guild_id       text,
  error_message  text        not null default '',
  error_code     text        not null default 'UNKNOWN',
  failure_stage  text        not null default 'unknown',
  metadata       jsonb       default '{}'::jsonb,
  retry_count    integer     not null default 0,
  last_retry_at  timestamptz,
  status         text        not null default 'failed'
                   check (status = any (array['failed','retrying','resolved'])),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint failed_dispatches_pkey primary key (id)
);

-- ============================================================
-- 2. Column / PK / type fixes for tables that drifted from 001-010
-- ============================================================

-- ---- whitelist: composite PK (title_key, source), drop sources/item, add cols
alter table whitelist add column if not exists source      text        not null default '';
alter table whitelist add column if not exists description text;
alter table whitelist add column if not exists updated_at  timestamptz not null default now();
alter table whitelist add column if not exists created_at  timestamptz not null default now();
alter table whitelist add column if not exists genres      jsonb       default '[]'::jsonb;
alter table whitelist drop column if exists sources;
alter table whitelist drop column if exists item;

do $$
begin
  -- Replace single-column PK (title_key) with composite (title_key, source)
  if exists (
    select 1 from pg_constraint c
    join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
    where c.conrelid = 'whitelist'::regclass and c.contype = 'p'
      and array_length(c.conkey, 1) = 1 and a.attname = 'title_key'
  ) then
    alter table whitelist drop constraint whitelist_pkey;
    alter table whitelist add primary key (title_key, source);
  end if;
end $$;
-- ---- dispatch_history: PK chapter_url (drop id), add chapter_title
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_name = 'dispatch_history' and column_name = 'id'
  ) then
    alter table dispatch_history drop constraint if exists dispatch_history_pkey;
    alter table dispatch_history drop constraint if exists dispatch_history_chapter_url_key;
    alter table dispatch_history drop column if exists id;
    alter table dispatch_history add primary key (chapter_url);
  end if;
end $$;
alter table dispatch_history add column if not exists chapter_title text;

-- ---- guild_settings: PK guild_id (drop id)
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_name = 'guild_settings' and column_name = 'id'
  ) then
    alter table guild_settings drop constraint if exists guild_settings_pkey;
    alter table guild_settings drop constraint if exists guild_settings_channel_id_key;
    alter table guild_settings drop column if exists id;
    alter table guild_settings add primary key (guild_id);
  end if;
end $$;
alter table guild_settings add column if not exists updated_at timestamptz not null default now();

-- ---- source_health: timestamps + status CHECK
alter table source_health add column if not exists updated_at timestamptz not null default now();
alter table source_health add column if not exists created_at timestamptz not null default now();
do $$
begin
  alter table source_health drop constraint if exists source_health_status_check;
  alter table source_health
    add constraint source_health_status_check
    check (status = any (array['HEALTHY','DEGRADED','DOWN','BLOCKED','RATE_LIMITED']));
end $$;

-- ---- cron_run_status: status jsonb + extra columns
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_name = 'cron_run_status' and column_name = 'status' and data_type = 'text'
  ) then
    alter table cron_run_status alter column status type jsonb using to_jsonb(status);
  end if;
end $$;
alter table cron_run_status add column if not exists chapters_sent integer default 0;
alter table cron_run_status add column if not exists matched      integer default 0;
alter table cron_run_status add column if not exists duration     numeric;

-- ---- recent_chapters: description
alter table recent_chapters add column if not exists description text;

-- ---- Standardize source columns: enum manga_source -> text (live uses text)
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_name = 'recent_chapters' and column_name = 'source' and data_type = 'USER-DEFINED'
  ) then
    alter table recent_chapters alter column source type text using source::text;
  end if;
  if exists (
    select 1 from information_schema.columns
    where table_name = 'dispatch_history' and column_name = 'source' and data_type = 'USER-DEFINED'
  ) then
    alter table dispatch_history alter column source type text using source::text;
  end if;
  if exists (
    select 1 from information_schema.columns
    where table_name = 'source_health' and column_name = 'source' and data_type = 'USER-DEFINED'
  ) then
    alter table source_health alter column source type text using source::text;
  end if;
end $$;

-- ============================================================
-- 3. Indexes for API query patterns
-- ============================================================
create index if not exists idx_recent_chapters_source_origin
  on recent_chapters (source, origin);
create index if not exists idx_dispatch_history_sent_at
  on dispatch_history (sent_at desc);
create index if not exists idx_failed_dispatches_status_created
  on failed_dispatches (status, created_at desc);
create index if not exists idx_whitelist_source
  on whitelist (source);
create index if not exists idx_dispatch_claims_status
  on dispatch_claims (status);

-- ============================================================
-- 4. RPC rewrites
-- ============================================================
-- sync_whitelist: now matches live whitelist (composite PK, source NOT NULL,
-- no sources column). The old body referenced a dropped column and used an
-- invalid single-column conflict target -> it errored on every call.
drop function if exists sync_whitelist(text);
create or replace function sync_whitelist(p_rows text)
returns setof whitelist
language plpgsql
as $$
declare
  rows_json jsonb := p_rows::jsonb;
  elem jsonb;
begin
  for elem in select jsonb_array_elements(rows_json)
  loop
    insert into whitelist (title_key, title, cover, source, origin, status, rating, genres, description)
    values (
      elem->>'title_key',
      elem->>'title',
      elem->>'cover',
      coalesce(elem->>'source', 'ikiru'),
      elem->>'origin',
      elem->>'status',
      elem->>'rating',
      coalesce(elem->'genres', '[]'::jsonb),
      elem->>'description'
    )
    on conflict (title_key, source) do update
    set title       = excluded.title,
        cover       = coalesce(excluded.cover, whitelist.cover),
        origin      = coalesce(excluded.origin, whitelist.origin),
        status      = coalesce(excluded.status, whitelist.status),
        rating      = coalesce(excluded.rating, whitelist.rating),
        genres      = coalesce(excluded.genres, whitelist.genres),
        description = coalesce(excluded.description, whitelist.description),
        updated_at  = now();
  end loop;
  return query
    select * from whitelist
    where (title_key, source) in (
      select r->>'title_key', r->>'source'
      from jsonb_array_elements(rows_json) as r
    );
end;
$$;

-- upsert_title_last_chapter: was a no-op stub; title_last_chapters now exists.
-- Keeps the original 3-arg signature so nothing breaks if it is ever called.
-- SAFE CAST: chapter strings like "Extra"/"TBA" would throw on ::double precision.
drop function if exists upsert_title_last_chapter(text, text, text);
create or replace function upsert_title_last_chapter(
  p_title_key   text,
  p_chapter     text,
  p_instance_id text
)
returns void
language plpgsql
as $$
begin
  insert into title_last_chapters (title_key, chapter_number)
  values (
    p_title_key,
    case
      when p_chapter is null or p_chapter = '' then 0
      when p_chapter ~ '^[0-9]+(\.[0-9]+)?$' then p_chapter::double precision
      else 0
    end
  )
  on conflict (title_key) do update
  set chapter_number = excluded.chapter_number,
      updated_at     = now();
end;
$$;

-- upsert_source_health_batch: dead code (storage/health.py uses a direct
-- upsert). Drop to avoid a misleading, unused RPC.
drop function if exists upsert_source_health_batch(
  text[], text[], integer[], timestamptz[], text[], timestamptz[], timestamptz[], integer[], integer[], integer[]
);
