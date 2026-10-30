-- Migration 026: excluded_titles cover/series_url were referenced in code (storage/excluded_titles.py:149)
-- but never added in 013. Fresh local DBs hit "column cover does not exist".
ALTER TABLE excluded_titles ADD COLUMN IF NOT EXISTS cover text;
ALTER TABLE excluded_titles ADD COLUMN IF NOT EXISTS series_url text;
