-- Postgres helper for analytics retention: normalize_title_key(text)
-- Mirrors Python app/utils/text.py: html.unescape + lower + [^a-z0-9]+ -> ' ' + collapse
CREATE OR REPLACE FUNCTION normalize_title_key(input TEXT) RETURNS TEXT AS $$
SELECT trim(regexp_replace(regexp_replace(lower(COALESCE(input,'')), '[^a-z0-9]+', ' ', 'g'), '\s+', ' ', 'g'));
$$ LANGUAGE SQL IMMUTABLE;
