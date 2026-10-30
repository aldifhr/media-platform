-- 083_drop_excluded_titles.sql
-- Exclude-list feature removed entirely (backend + frontend + shared + docs).
-- The table is no longer read or written by any app code:
--   * app/api/admin/excluded.py (CRUD routes)      — deleted
--   * app/storage/excluded_titles.py (storage)     — deleted
--   * app/services/excluded_service.py             — deleted
--   * RSS filtering (rss_query.build_filter)       — excl_keys param removed
--   * collect.py exclude filter block              — removed
--   * per-guild dispatch exclusion                 — removed
--   * app.config.EXCLUDE_SOURCES                   — removed
-- 073 already dropped the TAMAT/completed columns (reason, is_completed) and
-- 035 already dropped guild_settings.excluded_titles, so only the table and
-- its indexes/constraints remain.
--
-- Idempotent: IF EXISTS everywhere. Safe on any DB, any number of times.

DROP TABLE IF EXISTS excluded_titles;
