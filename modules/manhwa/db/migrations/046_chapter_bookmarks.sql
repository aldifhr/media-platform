-- 046_chapter_bookmarks.sql
-- Chapter bookmarks — per-session reading position (BE API /api/v1/bookmarks)
-- Fixes fresh install 500: table was used by app/services/bookmark.py but never migrated

create table if not exists chapter_bookmarks (
  title_key      text not null,
  chapter_number double precision not null,
  chapter_url    text not null default '',
  session_hash   text not null,
  source         text not null default '',
  position_pct   double precision not null default 0.0,
  title          text not null default '',
  cover          text not null default '',
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  primary key (title_key, chapter_number, session_hash)
);

create index if not exists idx_chapter_bookmarks_session_hash
  on chapter_bookmarks (session_hash, updated_at desc);
create index if not exists idx_chapter_bookmarks_title_key
  on chapter_bookmarks (title_key);
