-- Migration 030: canonical_series was referenced in app/storage/canonical.py but never created
-- in fresh DBs (only on VPS). Create it so _load() doesn't hit "relation does not exist".
CREATE TABLE IF NOT EXISTS public.canonical_series (
    title_key text PRIMARY KEY,
    canonical_title_key text NOT NULL,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_canonical_series_canonical ON public.canonical_series (canonical_title_key);
