-- Migration 029: normalize whitelist title_key dash vs space drift.
-- Old code inserted raw title_key (dash: divine-beast-kindergarten) while WhitelistRow normalizes to space.
-- This caused two PKs for same series and isWhitelisted miss. Normalize all to space, lower, collapsed.
-- Force to lower + replace non-alnum with space, collapse. Keep source as-is.
-- If both dash and space variants exist for same (title_key normalized, source), keep the most recent (max created_at) and drop the other.

-- Normalize title_key in place where it contains dash or uppercase
UPDATE whitelist
SET title_key = lower(trim(regexp_replace(title_key, '[^a-z0-9]+', ' ', 'g'))),
    updated_at = now()
WHERE title_key ~ '[^a-z0-9 ]' OR title_key <> lower(title_key);

-- Dedupe: if normalized produced duplicates for same (title_key, source), keep newest
DELETE FROM whitelist a USING whitelist b
WHERE a.title_key = b.title_key
  AND a.source = b.source
  AND a.ctid < b.ctid
  AND a.created_at < b.created_at;

-- Also normalize excluded_titles for consistency
UPDATE excluded_titles
SET title_key = lower(trim(regexp_replace(title_key, '[^a-z0-9]+', ' ', 'g')))
WHERE title_key ~ '[^a-z0-9 ]' OR title_key <> lower(title_key);

DELETE FROM excluded_titles a USING excluded_titles b
WHERE a.title_key = b.title_key
  AND a.source = b.source
  AND a.ctid < b.ctid;
