# Repository Inventory — Phase 0 Audit

Audit date: 2026-10-30 · Auditor: ENI · Method: on-disk inspection + git history, READMEs treated as unverified.

Two source repositories are consolidated into `media-platform`. Neither source repo is modified.

| Repo | Local path | Remote | HEAD | Role |
|---|---|---|---|---|
| media-scrapers | `/root/projects/media-scrapers` | github.com/aldifhr/media-scrapers | `4c481bb` | Media release notifier (pahe) |
| manhwa-scanner | `/root/projects/manhwa-scanner` | github.com/aldifhr/manhwa-scanner | `aba1c4b` | Manhwa tracker (reader + catalog + dispatch) |
| media-platform | `/root/projects/media-platform` | github.com/aldifhr/media-platform | *(empty)* | Target monorepo |

## media-scrapers

Single active source (pahe). No database, no Redis — all state is file-backed JSON under `data/`.

```
media.py                FastAPI app + in-process scheduler lifespan (ENTRYPOINT)
scheduler.py            supervised in-process polling loop
source_health.py        consecutive-failure tracking + auto-quarantine
common/
  env.py                .env loader (real environment always wins over file)
  auth.py               shared bearer-token guard
  discord.py            webhook + bot-channel delivery, one retry policy
  log.py                structured logging to stdout
  state.py              atomic file-backed seen-state
pahe/
  main.py               standalone FastAPI app (NO scheduler) for direct use
  scanner/
    service.py          the check: scrape -> filter -> notify -> mark seen
    rules.py            freshness rule (one definition, two callers)
    scraper.py          feed + post-page parsing
    notify.py           Discord embed construction + delivery + thread routing
    translate.py        synopsis translation (MyMemory) + disk cache
    main.py             the /pahe router
data/                   pahe_seen.json, source_health.json, translated.json (state)
tests/test_regressions.py
```

- Runtime: one uvicorn process. Scheduler is a lifespan task inside the same process.
- Delivery: Discord **bot REST API** (`POST /channels/{id}/messages`), not webhooks. Optional thread routing by release-year / genre.
- State: `data/*.json` with atomic writes + `.lock` files. No durability across hosts.
- Tooling: pytest (`tests/test_regressions.py`), ruff, compileall. `requirements.txt` + `pyproject.toml`.

## manhwa-scanner

Mature modular app. Three deployables already separated by folder: `apps/backend` (FastAPI), `apps/frontend` (Next.js reader), `apps/ops` (ops dashboard, Vercel).

```
apps/backend/app/
  main.py               FastAPI app, lifespan (migrate + start cron-worker thread), uvicorn entry
  config.py boot_config.py
  api/                  admin/ audit auth catalog/ continue_reading cover cron dispatch/
                        error_logs health openapi public_stats recommended rss/ system
  core/                 cache cover fcfs origin rating_utils redis resilience rss_cache
                        sql_fragments ssrf text time
  cron/                 collectors/{ikiru,shinigami} collect dispatch_alert dispatch_mod
                        dispatch_watchdog enrich/ gap_detector notify_common pipeline
                        series_meta_sync source_alert source_result watchdog
  db/                   adapter migrate_utils migrations/*.sql (87 files, custom SQL runner)
  discord/              client gateway_sender http router embeds
  scrapers/ikiru/
  services/dispatch/    channels backfill runner retry service senders
  metrics_prometheus.py
apps/backend/tests/     81 test files
apps/frontend/          Next.js 16.3.4 reader (React 19, Tailwind v4, TanStack, zustand, zod v4)
apps/ops/               ops dashboard
packages/shared/        shared TS types + generated OpenAPI client
```

- Runtime today: **one** uvicorn process per PM2 instance (`workers=1`). API + cron worker (daemon `threading.Thread`) + Discord interactions endpoint all share that process. PM2 handles clustering.
- Migrations: raw `.sql` files run by a custom runner (`db/migrate_utils.py`), guarded by `pg_advisory_lock(1234567890)`, tracked in a `schema_migrations`-style table. **Not Alembic.**
- Discord: inbound via **HTTP interactions endpoint** (Ed25519 `discord_interactions.verify_key`), outbound via bot REST + gateway-websocket fallback.
- Tooling: pytest, ruff, import-linter (`.importlinter`), husky/lint-staged, pnpm workspaces, uv (`uv.lock`).

## Process/entrypoint summary

| Concern | media-scrapers | manhwa-scanner |
|---|---|---|
| API | media.py (FastAPI, :3100 prod) | apps/backend uvicorn (:3000) |
| Scheduler | in-process lifespan loop | in-process daemon thread |
| Worker | same process | same process (ThreadPoolExecutor) |
| Discord inbound | none (push-only) | HTTP interactions endpoint |
| Discord outbound | bot REST | bot REST + gateway fallback |
| Frontend | none | Next.js (:3002) + ops (Vercel) |
| Datastore | file JSON | PostgreSQL + Redis (cache) |
