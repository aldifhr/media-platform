-- 022_dispatch_fcfs_latest_sent.sql
-- Fix re-notify bug: ikiru re-updates an OLD chapter (touches updated_time to
-- "now" + rotates the chapter URL), so the existing URL-only dedupe misses it
-- and Discord re-fires a notification for a chapter already sent weeks ago.
--
-- Root cause:
--   1. dispatch_history had NO fcfs_key column, so title+chapter dedupe was dead.
--   2. whitelist.latest_sent_chapter was updated on send but NEVER checked.
--   3. collect.py kept any chapter whose updated_time fell in the 24h window,
--      even if that timestamp was a re-touch of an ancient chapter.
--
-- Fix:
--   A. Add stable fcfs_key (normalized title + chapter) to dispatch_history.
--      Same title+chapter from ANY url/source is now deduped permanently.
--   B. Add whitelist.latest_sent_chapter and use it as a hard ceiling:
--      a chapter is NEVER notified if its number <= the highest already sent
--      for that title+source. Re-touched old chapters (108 "re-released" as
--      105, or 108 re-updated) are blocked regardless of URL/timestamp tricks.

-- A. dispatch_history.fcfs_key
alter table dispatch_history add column if not exists fcfs_key text;
create unique index if not exists idx_dispatch_history_fcfs_key
  on dispatch_history (fcfs_key);

-- B. whitelist.latest_sent_chapter (per source, since a title can be tracked
--    on both ikiru + shinigami and progress differs per source)
alter table whitelist add column if not exists latest_sent_chapter numeric not null default 0;

-- Backfill latest_sent_chapter from existing dispatch_history so the ceiling
-- guard immediately protects already-notified chapters (e.g. the ch108 that
-- ikiru just re-touched). dispatch_history stores the chapter number inside
-- chapter_title ("Chapter 108") / chapter_url ("/chapter-108.<id>/"); extract it.
update whitelist w set latest_sent_chapter = coalesce((
  select max(
    (regexp_match(dh.chapter_title, '(\d+(?:\.\d+)?)'))[1]::numeric
  )
  from dispatch_history dh
  where dh.title_key = w.title_key
    and dh.source = w.source
    and dh.chapter_title ~ '\d'
), 0)
where exists (
  select 1 from dispatch_history dh
  where dh.title_key = w.title_key and dh.source = w.source
);
