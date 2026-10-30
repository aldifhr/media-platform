
-- keep manga_metadata as VIEW for backward compat (old cron still SELECT manga_metadata)
-- backfill: INSERT missing rows from manga_metadata → series_meta

-- 1. Backfill series_meta from manga_metadata (only shinigami rows, normalize title_key if needed)
INSERT INTO series_meta (title_key, source, cover, genres, origin, created_at, updated_at)
SELECT
  LOWER(m.manga_id) as title_key,
  'shinigami' as source,
  m.cover,
  m.genres,
  m.origin,
  COALESCE(m.created_at, now()),
  COALESCE(m.updated_at, now())
FROM manga_metadata m
LEFT JOIN series_meta s ON s.title_key = LOWER(m.manga_id) AND s.source = 'shinigami'
WHERE s.title_key IS NULL
ON CONFLICT (title_key, source) DO NOTHING;

-- 2. Keep manga_metadata readable via view (optional, not dropping table yet — drop after 1 week stable)
-- To actually merge, uncomment below: rename old table and create view
-- ALTER TABLE manga_metadata RENAME TO manga_metadata_legacy;
-- CREATE VIEW manga_metadata AS SELECT title_key as manga_id, title_key, source, cover, genres, origin, created_at, updated_at FROM series_meta WHERE source = 'shinigami';
