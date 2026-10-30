-- Migration 074: Normalize voratoon title_key from series_url and dedup
-- ROOT CAUSE: FE submitted title_key from display title (with 's, -, etc.)
-- while series_url path has the canonical slug. After slugify, they differ → duplicate rows.

-- Step 1: Normalize title_key from series_url path for voratoon
UPDATE whitelist w
SET title_key = trim(
    both '-'
    from regexp_replace(
        lower(
            split_part(
                split_part(w.series_url, chr(63), 1),
                '/',
                array_length(string_to_array(split_part(w.series_url, chr(63), 1), '/'), 1)
            )
        ),
        '[^a-z0-9]+',
        '-',
        'g'
    )
)
WHERE w.source = 'voratoon'
  AND w.series_url IS NOT NULL AND w.series_url <> ''
  AND w.series_url LIKE 'https://%/%';

-- Step 2: Delete duplicates keeping the row with highest latest_sent_chapter,
-- then most recent created_at
DELETE FROM whitelist
WHERE id IN (
  SELECT id FROM (
    SELECT id,
      ROW_NUMBER() OVER (
        PARTITION BY title_key, source
        ORDER BY COALESCE(latest_sent_chapter, 0) DESC, created_at DESC
      ) as rn
    FROM whitelist
  ) ranked
  WHERE rn > 1
);
