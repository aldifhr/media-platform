# Migration Plan — Phase 0 Audit

Audit date: 2026-10-30 · Incremental, verifiable, non-destructive. Working system after every phase.

## Decisions
LO's architectural calls are logged in `docs/audit/decisions.md` (ADR-001..005). Binding ones: HTTP-interactions Discord (not discord.py), pahe-only media, pahe bot-API-only delivery, shared env = manhwa contract, bot-config UI hosted in the ops dashboard.

## Ground rules
- Do not push to source repos; they stay as reference + rollback.
- Do not touch production DB/config/deploy during migration.
- Preserve business logic; change only with a concrete technical reason + tests.
- One queue framework only. No Kafka/RabbitMQ/K8s.
- Small verifiable changes; run tests + ruff after each phase.

## Target structure mapping

```
media-platform/
  apps/api        <- manhwa apps/backend (FastAPI) + media media.py routes
  apps/discord    <- NEW process: interactions endpoint + delivery (token isolated here)
  apps/worker     <- NEW process: scheduler + scrapers + notification engine
  apps/frontend   <- manhwa apps/frontend (Next.js 16) unchanged
  apps/ops        <- manhwa apps/ops, EXTENDED with bot-config UI (guilds/subscriptions/routes/sources) — ADR-005
  modules/manhwa/ <- domain sources services repositories jobs commands api tests
  modules/media/  <- domain sources services repositories jobs commands api tests (pahe)
  core/           <- config database queue events discord notifications scheduler cache
                      security logging observability
  packages/shared <- existing shared TS + OpenAPI client
  migrations/     <- platform manhwa media
  tests/          <- integration contract e2e
  scripts/ deploy/ docs/
```

## Phases

**Execution order (LO, 2026-10-30):** Phase 4 → **manhwa** → media (pahe). Manhwa is primary and already on the Discord bot, so it is stood up first. (Phase 5/6 numbering below is the original audit order; execute manhwa before media.)

**Phase 0 — Audit (THIS).** Done: 7 docs in `docs/audit/`. No code moved.

**Phase 1 — Monorepo foundation.**
- Scaffold `media-platform` (pyproject with uv workspaces, pnpm-workspace, Makefile, .env.example, CI). **No Docker** — Postgres + Redis run natively on the VPS (ADR-006).
- Git-history secret scan on both source repos; rotate anything exposed (manhwa PAT in remote URL, any Discord tokens/webhooks).
- Copy source repos in as reference under `docs/reference/` or git-subtree them read-only. No logic changes yet.
- Deliverable: empty-but-building monorepo, CI green on a smoke test.

**Phase 2 — Shared core infrastructure.**
- `core/config`, `core/database` (async engine + the existing SQL runner or Alembic bridge), `core/cache` (lift `core/redis.py`), `core/logging`, `core/observability`.
- `core/queue`: pick ONE. Recommend **arq** on Redis (async-native, fits FastAPI). Keep Redis cache usage intact.
- `core/scheduler`: distributed locks (lift `cron_locks` + advisory-lock pattern); single scheduler process only.
- `core/events` + `core/notifications`: event envelope (`event_id, event_type, version, source, occurred_at, payload`) + `notification_outbox` table.
- Source Registry abstraction wrapping existing scrapers (no rewrite).
- Deliverable: core importable, unit tests for queue/lock/outbox.

**Phase 3 — Discord bot process.**
- Stand up `apps/discord`. Decision A (HTTP interactions) unless LO chooses B.
- Port manhwa command routing + delivery (REST+gateway fallback) into `core/discord` + `modules/*/commands`.
- Token only in this process. Add command groups backed by real domain functions only.
- Deliverable: interactions endpoint verifies + routes; delivery sends; token absent from api/worker env.

**Phase 4 — Notification engine.**
- Scraper -> normalize -> outbox (transactional) -> engine -> guild subscription lookup -> channel routing -> delivery -> history.
- Idempotency, dedup, retry w/ exp backoff + jitter, max retries, dead-letter, rate limit, restart recovery.
- Deliverable: integration tests (duplicate event -> one notification; Discord down -> queued, not lost; worker restart -> job survives).

**Phase 5 — Media module (pahe).**
- Wrap pahe scraper behind Source Registry. Decide seen-state durability: move `data/pahe_seen.json` -> `media` schema table (with a one-time import + file fallback during cutover).
- Preserve freshness rule, filters, thread routing, translate cache, health/quarantine.
- Deliverable: pahe runs under worker; behavior identical (regression tests).

**Phase 6 — Manhwa module.**
- Move collectors (ikiru, shinigami), cron pipeline, dispatch engine, enrich, gap-fill into `modules/manhwa` + `apps/worker`.
- Preserve FCFS, normalization, claims, retry, watchdog, 24h feed.
- Deliverable: manhwa scraping + dispatch run under worker; API serves reads.

**Phase 7 — Database consolidation.**
- Snapshot live schema (`\dt`, indexes, constraints). Backup first.
- Create `platform`/`manhwa`/`media` schemas; move tables; add FKs; preserve PKs/uniques/indexes/dedup history/timestamps.
- Decide Alembic adoption vs. keep SQL runner (document deviation).
- Deliverable: migration up + down tested on a restore; record counts preserved.

**Phase 8 — API + frontend compatibility.**
- Compatibility routes for reader/catalog/RSS/image-proxy/dashboard/media endpoints. Reader frontend unchanged; ops dashboard gains the bot-config UI (ADR-005).
- Deliverable: contract tests pass; frontend builds against new API; bot-config UI manages guilds/subscriptions/routes/sources.

**Phase 9 — Integration + regression tests.**
- All source adapters, normalization, dedup, ordering, filtering, routing, retries, dead-letter, migrations, API compat, command behavior, source health, scheduler concurrency.
- The 8 integration scenarios from the prompt.
- Deliverable: full suite green; ruff clean; type checks pass.

**Phase 10 — Deployment prep.**
- systemd units `platform-api`, `platform-discord`, `platform-worker` on the VPS (journald logs); Caddy reverse proxy; health checks; native Postgres + Redis; frontends (reader + bot-config) on Vercel; backup + rollback docs; env examples. **No Docker.**
- Do NOT auto-deploy. Stage + verify, then hand off.

## Per-phase report format
Completed · Files changed · Tests passed · Tests failed · Remaining risks · Next phase.
