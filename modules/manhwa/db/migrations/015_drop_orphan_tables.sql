-- 015_drop_orphan_tables.sql
-- Drops tables that are no longer referenced by any app code (audited
-- 2026-07-22 against live DB + grep of app/). These were sisa from the
-- Supabase era / earlier designs. Verified safe before dropping:
--   * 0 rows (or stale, e.g. live_events last write 2026-07-10) AND
--   * no `table("<name>")` / model reference anywhere in app/.
-- NOTE: whitelist_entries is INTENTIONALLY KEPT — add_whitelist_entries()
-- in app/storage/whitelist.py is still active (used by tasks.py + dashboard).
--
-- Run idempotently: DROP TABLE IF EXISTS ... CASCADE.

drop table if exists public.cron_locks                 cascade;
drop table if exists public.cron_logs                  cascade;
drop table if exists public.channel_validation_cache   cascade;
drop table if exists public.read_progress              cascade;
drop table if exists public.scrape_history             cascade;
drop table if exists public.scraper_stats              cascade;
drop table if exists public.title_last_chapters        cascade;
drop table if exists public.user_follows               cascade;
drop table if exists public.app_settings               cascade;
drop table if exists public.live_events                cascade;
