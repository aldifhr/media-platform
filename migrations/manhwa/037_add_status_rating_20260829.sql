-- Migration 037 (2026-08-29): add status + rating columns to recent_chapters
-- Both are enriched per-source (ikiru/shinigami via enrich(), voratoon via scraper).
ALTER TABLE recent_chapters ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT '';
ALTER TABLE recent_chapters ADD COLUMN IF NOT EXISTS rating text NOT NULL DEFAULT '';
