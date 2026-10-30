-- Migration 036 (2026-08-29): add genres column to recent_chapters
-- Genres are scraped per-source (ikiru/shinigami/voratoon) and passed through
-- to RSS. jsonb to store a list of genre name strings (Supabase client sends
-- Python lists as jsonb, so the column type must match).
ALTER TABLE recent_chapters DROP COLUMN IF EXISTS genres;
ALTER TABLE recent_chapters ADD COLUMN genres jsonb NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX IF NOT EXISTS idx_recent_chapters_genres ON recent_chapters USING GIN (genres);
