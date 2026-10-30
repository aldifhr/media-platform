-- 016_whitelist_unique_constraints.sql
-- ROOT CAUSE (same class of bug as 014): live DB was provisioned WITHOUT
-- the composite unique constraint that app code relies on for
-- `.upsert(on_conflict="title_key,source")`. add_whitelist_entries() in
-- app/storage/whitelist.py upserts to `whitelist` with on_conflict=title_key,source,
-- but the live `whitelist` table had NO PK/unique at all -> every add failed
-- with "there is no unique or exclusion constraint matching the ON CONFLICT
-- specification". whitelist_entries (add_whitelist_entries' sibling) also lacked it.
--
-- ALSO: the live `whitelist` table was missing the `url` and `permalink`
-- columns that WhitelistRow.to_db() emits, so even after the constraint was
-- added, upserts failed with "column url of relation whitelist does not exist".
-- Migration 011 added several columns but the manually-provisioned live DB
-- never received them. This migration adds the columns + constraints
-- idempotently.
--
-- Safe to re-run: every statement is guarded.

begin;

-- whitelist: add missing columns emitted by WhitelistRow.to_db()
alter table whitelist add column if not exists url          text;
alter table whitelist add column if not exists permalink    text;

-- whitelist: composite unique (title_key, source)
do $$
begin
  -- drop any pre-existing single-column PK/unique on title_key only
  if exists (
    select 1 from pg_constraint c
    join pg_class rel on rel.oid = c.conrelid
    where rel.relname = 'whitelist' and c.contype in ('p','u')
      and not (pg_get_constraintdef(c.oid) like '%title_key, source%'
               or pg_get_constraintdef(c.oid) like '%title_key,source%')
  ) then
    alter table whitelist drop constraint if exists whitelist_pkey;
    alter table whitelist drop constraint if exists whitelist_title_key_key;
  end if;
  if not exists (
    select 1 from pg_constraint c
    join pg_class rel on rel.oid = c.conrelid
    where rel.relname = 'whitelist' and c.contype in ('p','u')
      and (pg_get_constraintdef(c.oid) like '%title_key, source%'
           or pg_get_constraintdef(c.oid) like '%title_key,source%')
  ) then
    alter table whitelist
      add constraint whitelist_title_key_source_key unique (title_key, source);
  end if;
end $$;

-- whitelist_entries: composite unique (title_key, source) for parity
do $$
begin
  if not exists (
    select 1 from pg_constraint c
    join pg_class rel on rel.oid = c.conrelid
    where rel.relname = 'whitelist_entries' and c.contype in ('p','u')
      and (pg_get_constraintdef(c.oid) like '%title_key, source%'
           or pg_get_constraintdef(c.oid) like '%title_key,source%')
  ) then
    alter table whitelist_entries
      add constraint whitelist_entries_title_key_source_key unique (title_key, source);
  end if;
end $$;

commit;
