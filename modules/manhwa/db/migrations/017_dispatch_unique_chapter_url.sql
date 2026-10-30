-- 017_dispatch_unique_chapter_url.sql
-- Same class of bug as 014 / 016: live DB was provisioned WITHOUT the
-- UNIQUE(chapter_url) constraint that dispatch upserts rely on
-- (on_conflict="chapter_url"). mark_claimed() / send-pass upsert to
-- dispatch_claims and dispatch_history, but both tables had NO unique
-- constraint -> every call failed with
-- "there is no unique or exclusion constraint matching the ON CONFLICT
-- specification".
--
-- Audit of all .upsert(on_conflict=...) in app/ (2026-07-22):
--   recent_chapters    -> UNIQUE(chapter_url)            [present]
--   manga_metadata      -> PK(title_key)                  [present]
--   source_health       -> PK(source)                    [present]
--   dashboard_snapshot  -> PK(id)                         [present]
--   whitelist           -> UNIQUE(title_key, source)      [added 016]
--   whitelist_entries   -> UNIQUE(title_key, source)      [added 016]
--   dispatch_claims     -> UNIQUE(chapter_url)            [ADDED HERE]
--   dispatch_history     -> UNIQUE(chapter_url)            [ADDED HERE]
-- (guild_settings / excluded_titles upsert without on_conflict -> no constraint needed)
--
-- Live rows verified dedup-free before adding (0 duplicate chapter_url groups).
-- Safe to re-run: each constraint add is guarded by an existence DO-block.

begin;

do $$
begin
  if not exists (
    select 1 from pg_constraint c
    join pg_class r on r.oid = c.conrelid
    where r.relname = 'dispatch_claims' and c.contype = 'u'
      and pg_get_constraintdef(c.oid) like '%chapter_url%'
  ) then
    alter table dispatch_claims
      add constraint dispatch_claims_chapter_url_key unique (chapter_url);
  end if;
end $$;

do $$
begin
  if not exists (
    select 1 from pg_constraint c
    join pg_class r on r.oid = c.conrelid
    where r.relname = 'dispatch_history' and c.contype = 'u'
      and pg_get_constraintdef(c.oid) like '%chapter_url%'
  ) then
    alter table dispatch_history
      add constraint dispatch_history_chapter_url_key unique (chapter_url);
  end if;
end $$;

commit;
