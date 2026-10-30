-- 075_trim_excluded_titles.sql
-- One-off: trim leading/trailing spaces from excluded_titles.title and title_key
-- Root cause: FE submit tanpa TRIM → voratoon " Marriage..." vs shinigami "Marriage..."

BEGIN;

-- Trim spaces
UPDATE excluded_titles
SET title = TRIM(title),
    title_key = TRIM(title_key)
WHERE title LIKE ' %' OR title LIKE '% ';

-- Handle collisions: if TRIM makes duplicate (title_key+source), keep newest
DELETE FROM excluded_titles
WHERE id IN (
  SELECT id FROM (
    SELECT id,
      ROW_NUMBER() OVER (
        PARTITION BY title_key, source
        ORDER BY created_at DESC, id DESC
      ) as rn
    FROM excluded_titles
  ) ranked
  WHERE rn > 1
);

COMMIT;
