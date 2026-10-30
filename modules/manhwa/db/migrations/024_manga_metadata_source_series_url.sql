-- Migration 024: add source + series_url to manga_metadata
--
-- Backfill script backfill_ikiru_meta.py stores these fields, but the live
-- schema was missing both columns. Add them with safe defaults so existing
-- manga_metadata rows don't break enrich / RSS reads.

ALTER TABLE manga_metadata
  ADD COLUMN IF NOT EXISTS source text DEFAULT 'ikiru',
  ADD COLUMN IF NOT EXISTS series_url text;
