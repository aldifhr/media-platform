-- Migration 025: enforce composite uniqueness for recent_chapters
--
-- App-level dedup (_composite_key + _seen_ch) already prevents
-- URL-rotated re-touches from inserting duplicate rows for the same
-- (title_key, source, chapter_num). This migration makes the invariant
-- DB-enforced so concurrent rss-fetch runs cannot race-insert duplicates.
--
-- One-shots (chapter_num IS NULL) are excluded — they are never deduped
-- and must be allowed to have multiple rows per title/source.

-- Deduplicate existing rows first: keep newest id per composite key.
DELETE FROM recent_chapters
WHERE id IN (
  SELECT id FROM (
    SELECT id,
           ROW_NUMBER() OVER (
             PARTITION BY title_key, source, chapter_num
             ORDER BY id DESC
           ) AS rn
    FROM recent_chapters
    WHERE chapter_num IS NOT NULL
  ) s
  WHERE rn > 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_recent_chapters_composite_unique
  ON recent_chapters (title_key, source, chapter_num)
  WHERE chapter_num IS NOT NULL;
