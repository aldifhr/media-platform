-- Migration 027: dispatch_history cover/series_url were written by complete_dispatch_claim
-- but never added in fresh DBs (001 only had chapter_url/title_key/source/sent_at). Local
-- fresh installs hit "column cover does not exist" on GET /api/reader/dispatch-history.
ALTER TABLE dispatch_history ADD COLUMN IF NOT EXISTS cover text;
ALTER TABLE dispatch_history ADD COLUMN IF NOT EXISTS series_url text;
