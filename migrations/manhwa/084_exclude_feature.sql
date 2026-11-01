-- Exclude feature: per-series exclusion from raw feed with its own thread.

-- 1) exclude_channel_id on guild_settings
alter table public.guild_settings
    add column if not exists exclude_channel_id text;

-- 2) exclude_list table (title_key + source is the composite key)
create table if not exists public.exclude_list (
    guild_id    text    not null,
    title_key   text    not null,
    source      text    not null default '',
    user_id     text    not null,
    created_at  timestamptz not null default now(),
    primary key (guild_id, title_key, source)
);
