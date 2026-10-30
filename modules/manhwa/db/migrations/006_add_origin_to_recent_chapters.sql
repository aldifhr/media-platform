-- 006_add_origin_to_recent_chapters.sql
-- Add origin column to recent_chapters (JP/KR/CN country code)

alter table recent_chapters
  add column if not exists origin text;

create index if not exists idx_recent_chapters_origin
  on recent_chapters (origin);
