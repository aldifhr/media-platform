# Database Inventory — Phase 0 Audit

Audit date: 2026-10-30 · Tables extracted from migration SQL; live set must be confirmed with `\dt` in Phase 7.

## media-scrapers — no database

State is file-backed JSON only: `data/pahe_seen.json` (+`.lock`), `data/source_health.json`, `data/translated.json`. Atomic writes via `common/state.py`. **No PostgreSQL, no Redis, no Alembic.** Migrating pahe into the shared platform means deciding how seen-state becomes durable/shared (see migration-plan).

## manhwa-scanner — PostgreSQL, `public` schema

Migrations: **87** raw `.sql` files under `apps/backend/app/db/migrations/`, run by a custom runner (`db/migrate_utils.py`), guarded by `pg_advisory_lock(1234567890)`, tracked in a migrations table. Numbering is non-sequential in places (080–100 range; some duplicate numbers like `050`, `053`, `064`, `065`, `017`, `018`, `021`). **Not Alembic** — the target stack says Alembic, so Phase 7 must either adopt Alembic going forward or keep the SQL runner and document the deviation.

### Tables created across migrations (union)

Core domain:
- `recent_chapters` (+ partition `recent_chapters_p` / `recent_chapters_p_default`) — the rolling feed
- `dispatch_history`, `dispatch_claims`, `failed_dispatches` — dispatch engine
- `whitelist`, `excluded_titles` *(dropped by 083)*, `canonical_series`, `manga_metadata`
- `title_last_chapters`, `chapter_bookmarks` *(clicks dropped by 098)*, `continue_reading`, `read_progress`, `user_follows`
- `source_health`, `scrape_history`, `scraper_stats`
- `guild_settings`, `channel_validation_cache`, `live_events`
- `cron_run_status`, `cron_locks`, `cron_logs`, `dashboard_snapshot`
- `audit_log`, `error_logs`, `app_settings`
- `app_users` *(dropped by 050)*, `ab_test_events` *(dropped by 099)*, plus orphan drops in `015`, `073`

### Live-set caveat
Several tables are created then dropped by later migrations (`app_users`, `excluded_titles`, `chapter_clicks`, `ab_test_events`, completed-dispatch cleanup). The authoritative live schema is whatever `\dt` returns on the production DB. Phase 7 must snapshot `\dt` + indexes + constraints before any schema-split migration.

### Constraints/indexes worth preserving (dedup + integrity)
- FCFS key on dispatch (`010_fcfs_key`, `022`, `023`).
- Unique chapter URL (`017_dispatch_unique_chapter_url`).
- `recent_chapters` composite unique (`025`).
- Whitelist/excluded unique constraints (`014`, `016`, `018`, `038`).
- Trigram search (`040`), GIN on continue_reading (`043`), title_key join index (`079`).
- failed_dispatches id PK (`020`) + permanent-failure (`082`).

## Redis

`core/redis.py` — decoded client, 0.2s connect timeout, best-effort JSON cache with TTL. Used for RSS cache, dashboard/snapshot cache, cover cache. **Not a queue.** Target stack lists Redis as queue infra; today it is cache-only. Phase 2 decides whether Redis becomes the arq broker (recommended) while preserving the cache usage.

## Target schema plan (Phase 7)

One PostgreSQL instance, three schemas: `platform`, `manhwa`, `media`.

`platform` (new): guilds, guild_settings, modules, subscriptions, notification_routes, notification_deliveries, notification_outbox, source_registry.

`manhwa` (from existing): recent_chapters (+partition), dispatch_history, dispatch_claims, failed_dispatches, whitelist, canonical_series, manga_metadata, title_last_chapters, chapter_bookmarks, continue_reading, read_progress, user_follows, source_health, scrape_history, scraper_stats, guild_settings, channel_validation_cache, live_events, cron_run_status, cron_locks, cron_logs, dashboard_snapshot, audit_log, error_logs, app_settings.

`media` (new, from pahe file-state): seen-state table, source_health, translated cache (optional), release/notify history.

Migration must preserve PKs, unique constraints, indexes, dedup history, and timestamps; add FKs where appropriate; document rollback. No destructive change without a verified backup.
