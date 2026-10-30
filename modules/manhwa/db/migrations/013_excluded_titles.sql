-- Migration 013: persistently excluded titles (RSS "Exclude" feature).
-- A title added to the dashboard's RSS list via the Exclude button is stored
-- here so it is (a) filtered out of the /rss feed AND (b) skipped by the cron
-- collector (collect_recent_chapters) — so it is never scraped/dispatched
-- again until removed.
--
-- Key: (title_key, source). source='all' blocks the title on every source.
CREATE TABLE IF NOT EXISTS public.excluded_titles (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title_key  text NOT NULL,
    title      text,
    source     text NOT NULL DEFAULT 'all',
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_excl_tk_src
    ON public.excluded_titles (title_key, source);

-- Enforce one row per (title_key, source) so re-adds are idempotent.
CREATE UNIQUE INDEX IF NOT EXISTS uq_excl_tk_src
    ON public.excluded_titles (title_key, source);
