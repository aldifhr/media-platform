-- 100_exclude_no_all_source.sql
-- Exclude rows are now always scoped to ONE concrete source. There is no
-- 'all' scope anymore: app.config.EXCLUDE_SOURCES = ('shinigami','komiku').
--
-- Two data fixes:
--  1. source='all' rows are expanded into one row per concrete source, then
--     the 'all' row is deleted. With only two sources, 'all' meant exactly
--     "blocked on both", so the expansion preserves the original intent.
--  2. title_key is normalized to the canonical DASHED form
--     (slugify_title_key). Older rows were stored with spaces, which made
--     add (slugified) and remove (raw) disagree and produced duplicates.
--
-- Idempotent: re-running is a no-op once no 'all' / spaced rows remain.

BEGIN;

-- 1a. Expand 'all' -> concrete sources (ON CONFLICT keeps the existing row).
INSERT INTO excluded_titles (title_key, title, source, cover, series_url, created_at)
SELECT e.title_key, e.title, s.source, e.cover, e.series_url, e.created_at
FROM excluded_titles e
CROSS JOIN (VALUES ('shinigami'), ('komiku')) AS s(source)
WHERE e.source = 'all'
ON CONFLICT (title_key, source) DO NOTHING;

DELETE FROM excluded_titles WHERE source = 'all';

-- 2a. Normalize spaced title_keys to the canonical dashed form.
--     Mirrors app.utils.text.normalize_title_key: lowercase, non-alnum -> space,
--     collapse whitespace, then spaces -> dashes.
UPDATE excluded_titles
SET title_key = regexp_replace(
        regexp_replace(
          regexp_replace(lower(title_key), '[^a-z0-9]+', ' ', 'g'),
          '\s+', ' ', 'g'),
        ' ', '-', 'g')
WHERE title_key ~ '[ ]'
  AND title_key <> regexp_replace(
        regexp_replace(
          regexp_replace(lower(title_key), '[^a-z0-9]+', ' ', 'g'),
          '\s+', ' ', 'g'),
        ' ', '-', 'g');

-- 2b. Collapse rows that normalization made identical (keep the newest).
DELETE FROM excluded_titles
WHERE id IN (
  SELECT id FROM (
    SELECT id,
      ROW_NUMBER() OVER (
        PARTITION BY title_key, source
        ORDER BY created_at DESC, id DESC
      ) AS rn
    FROM excluded_titles
  ) ranked
  WHERE rn > 1
);

COMMIT;
