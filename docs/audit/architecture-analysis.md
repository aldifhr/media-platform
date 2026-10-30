# Architecture Analysis — Phase 0 Audit

Audit date: 2026-10-30 · How each system actually runs today vs. the target modular monolith.

## Current runtime model

Both systems are **single-process, in-process-scheduler** designs. Neither has a separate worker, and neither uses a real async job queue.

### media-scrapers
- One uvicorn process (`media.py`). Scheduler is a FastAPI **lifespan** task that polls pahe on an interval.
- `pahe/main.py` is a second, scheduler-less FastAPI app for standalone/direct use — not used in prod (systemd runs `media.py`).
- Failure isolation: `source_health.py` quarantines a source after N consecutive failures. With one source, isolation is trivial today but the pattern must generalize.
- No DB, no Redis. State = `data/*.json`.

### manhwa-scanner
- One uvicorn process per PM2 instance (`workers=1`; PM2 clusters). Inside it:
  - FastAPI API surface (`api/`).
  - Cron worker as a **daemon thread** started in `lifespan` (`main.py:117`), using `ThreadPoolExecutor` so one slow source can't block others (`cron/pipeline.py:318`).
  - Discord **interactions endpoint** (`main.py:298`).
- Migrations run at startup under `pg_advisory_lock(1234567890)` — only one instance migrates.
- Dispatch claiming uses `FOR UPDATE SKIP LOCKED` (atomic whitelisted deep-queue claim) + a `cron_locks` table for cron mutual exclusion.
- Redis is **cache only** (`core/redis.py`: `json_cache_set/get` with TTL, best-effort, 0.2s connect timeout). No queue semantics, no pub/sub for jobs.

## Deviations from the target architecture

| Target | Reality | Action |
|---|---|---|
| Separate api / discord / worker processes | All three concerns share one uvicorn process (manhwa); one process total (media) | Phase 2/3: split into `platform-api`, `platform-discord`, `platform-worker` |
| Async job queue (arq/Dramatiq) | No queue framework. Jobs are DB-coordinated (advisory locks, SKIP LOCKED, cron_locks) | Phase 2: introduce ONE queue (recommend arq) OR formalize the DB-outbox worker; do not run two queue systems |
| PostgreSQL transactional outbox for notifications | Dispatch is DB-backed already (dispatch_claims/history) but no generic outbox/envelope table | Phase 4: add `notification_outbox` + event envelope |
| discord.py Cogs + slash commands | Inbound is HTTP interactions (Ed25519 verify), not discord.py gateway. Outbound is REST + gateway fallback | Phase 3: DECISION — keep HTTP-interactions model (already works, signature-verified) vs. migrate to discord.py. See risk-assessment |
| One shared PostgreSQL, schemas platform/manhwa/media | manhwa uses `public` schema (bare + `public.` mixed); media has no DB | Phase 7: schema split is a migration, not a rewrite |
| Source Registry abstraction | None; scrapers hard-wired per app | Phase 2: new wrapper abstraction |

## What must NOT change (reliability contract)

- FCFS dedup semantics (`core/fcfs.py`) — no change without tests.
- Chapter normalization + ordering.
- Dispatch claim/retry/watchdog behavior.
- pahe freshness rule (`rules.py`) — one definition, two callers.
- Reader/catalog/RSS/image-proxy/dashboard API contracts (compatibility routes required).
- 24h rolling feed + gap-fill.

## Positive assets to carry forward

- `common/` (media) is a clean seam: env/auth/discord/log/state are already isolated — natural `core/` seeds.
- manhwa `discord/` + `services/dispatch/` is a mature delivery engine with REST+gateway fallback and 429 handling — reuse as the notification delivery core.
- `core/fcfs.py`, `core/ssrf.py`, `core/resilience.py` are production-hardened — lift as-is.
- Frontend already on the target stack (Next.js 16, React 19, Tailwind v4) — no rewrite.
