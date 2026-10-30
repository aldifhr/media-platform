-- Per-guild raw/whitelist feed channels.
--
-- guild_settings.channel_id is the legacy single feed. The operator now wants
-- two: RAW gets every new chapter (with an "Add to whitelist" button),
-- WHITELIST gets only whitelisted series. They are separate channels, so each
-- feed needs its own column.
--
-- A guild that never sets either column keeps the legacy behaviour: both
-- dispatch_mod.guild_setting() calls fall back to the env vars, and when those
-- are empty the feed is simply off. No backfill — pointing the old channel_id
-- at both feeds would double-notify every series.
alter table guild_settings
  add column if not exists raw_channel_id text,
  add column if not exists whitelist_channel_id text;
