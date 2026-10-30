-- 001_initial_schema.sql
-- Schema untuk be-ag-py — jalanin di Supabase SQL Editor
-- Cara: copy-paste ke https://supabase.com/dashboard/project/yqybccimwawcwifrpdnk/sql/new

-- ============================================================
-- ENUM
-- ============================================================
create type manga_source as enum ('ikiru', 'shinigami');

-- ============================================================
-- recent_chapters
-- ============================================================
create table if not exists recent_chapters (
  id            bigint generated always as identity primary key,
  chapter_url   text not null,
  title_key     text not null,
  title         text not null,
  chapter       text not null,
  chapter_num   numeric,              -- extracted from chapter for numeric sorting
  source        manga_source not null,
  cover         text,
  series_url    text,
  updated_time  timestamptz,
  created_at    timestamptz not null default now(),

  constraint recent_chapters_not_empty check (
    chapter_url <> '' and title_key <> '' and title <> '' and chapter <> ''
  ),
  unique (chapter_url)
);

create index if not exists idx_recent_chapters_updated_time
  on recent_chapters (updated_time desc);
create index if not exists idx_recent_chapters_title_key
  on recent_chapters (title_key);
create index if not exists idx_recent_chapters_source
  on recent_chapters (source);

-- ============================================================
-- dispatch_history
-- ============================================================
create table if not exists dispatch_history (
  id           bigint generated always as identity primary key,
  chapter_url  text not null,
  title_key    text not null default 'unknown',
  source       manga_source,          -- diisi biar filter failed-dispatches works
  sent_at      timestamptz not null default now(),
  created_at   timestamptz not null default now(),

  unique (chapter_url)
);

create index if not exists idx_dispatch_history_created_at
  on dispatch_history (created_at desc);
create index if not exists idx_dispatch_history_source
  on dispatch_history (source);

-- ============================================================
-- whitelist
-- ============================================================
create table if not exists whitelist (
  title_key text primary key,
  title     text not null,
  sources   jsonb not null default '[]'::jsonb,
  cover     text,
  status    text,
  rating    text,
  origin    text,
  item      jsonb          -- metadata snapshot: {status, rating, genres, description, cover}
);

create index if not exists idx_whitelist_sources
  on whitelist using gin (sources);

-- ============================================================
-- manga_metadata
-- ============================================================
create table if not exists manga_metadata (
  title_key    text primary key,
  cover        text,
  status       text,
  rating       text,
  genres       jsonb,
  description  text,
  origin       text,
  updated_at   timestamptz not null default now()
);

-- ============================================================
-- source_health
-- ============================================================
create table if not exists source_health (
  source               manga_source primary key,
  status               text not null default 'unknown',
  consecutive_failures integer not null default 0,
  disabled_until       timestamptz,
  last_error           text,
  last_success_at      timestamptz,
  last_checked_at      timestamptz,
  response_time_ms     integer,
  failures_today       integer not null default 0,
  successes_today      integer not null default 0
);

-- ============================================================
-- cron_run_status
-- ============================================================
create table if not exists cron_run_status (
  id         bigint generated always as identity primary key,
  status     text not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_cron_run_status_created_at
  on cron_run_status (created_at desc);

-- ============================================================
-- guild_settings
-- ============================================================
create table if not exists guild_settings (
  id         bigint generated always as identity primary key,
  channel_id text not null unique,
  guild_id   text,
  created_at timestamptz not null default now()
);

-- ============================================================
-- RPC: sync_whitelist
-- ============================================================
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
    insert into whitelist (title_key, title, cover, sources)
    values (
      elem->>'title_key',
      elem->>'title',
      elem->>'cover',
      coalesce(elem->'sources', '[]'::jsonb)
    )
    on conflict (title_key) do update
    set title   = excluded.title,
        cover   = coalesce(excluded.cover, whitelist.cover),
        sources = excluded.sources;
  end loop;
  return query
    select * from whitelist
    where title_key in (
      select r->>'title_key' from jsonb_array_elements(rows_json) as r
    );
end;
$$;

-- ============================================================
-- RPC: upsert_source_health_batch
-- ============================================================
drop function if exists upsert_source_health_batch(text[], text[], integer[], timestamptz[], text[], timestamptz[], timestamptz[], integer[], integer[], integer[]);
create or replace function upsert_source_health_batch(
  p_sources             text[],
  p_statuses            text[],
  p_consecutive_failures integer[],
  p_disabled_untils     timestamptz[],
  p_last_errors         text[],
  p_last_success_ats    timestamptz[],
  p_last_checked_ats    timestamptz[],
  p_response_time_ms    integer[],
  p_failures_todays     integer[],
  p_successes_todays    integer[]
)
returns void
language plpgsql
as $$
begin
  for i in 1 .. array_length(p_sources, 1)
  loop
    insert into source_health (
      source, status, consecutive_failures, disabled_until,
      last_error, last_success_at, last_checked_at,
      response_time_ms, failures_today, successes_today
    ) values (
      p_sources[i]::manga_source,
      coalesce(p_statuses[i], 'healthy'),
      coalesce(p_consecutive_failures[i], 0),
      p_disabled_untils[i],
      p_last_errors[i],
      p_last_success_ats[i],
      p_last_checked_ats[i],
      p_response_time_ms[i],
      coalesce(p_failures_todays[i], 0),
      coalesce(p_successes_todays[i], 0)
    )
    on conflict (source) do update
    set status               = coalesce(excluded.status, source_health.status),
        consecutive_failures = coalesce(excluded.consecutive_failures, source_health.consecutive_failures),
        disabled_until       = excluded.disabled_until,
        last_error           = excluded.last_error,
        last_success_at      = excluded.last_success_at,
        last_checked_at      = excluded.last_checked_at,
        response_time_ms     = coalesce(excluded.response_time_ms, source_health.response_time_ms),
        failures_today       = coalesce(excluded.failures_today, source_health.failures_today),
        successes_today      = coalesce(excluded.successes_today, source_health.successes_today);
  end loop;
end;
$$;

-- ============================================================
-- RPC: upsert_title_last_chapter
-- ============================================================
drop function if exists upsert_title_last_chapter(text, text, text);
create or replace function upsert_title_last_chapter(
  p_title_key    text,
  p_chapter      text,
  p_instance_id  text
)
returns void
language plpgsql
as $$
begin
  -- stub: bisa diisi dengan actual tracking table jika diperlukan
  -- untuk sekarang, no-op karena gak ada title_last_chapter table
end;
$$;
