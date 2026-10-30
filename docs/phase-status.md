# Phase Status

Live checklist. Updated as phases complete. `✅` done · `🚧` in progress · `⬜` pending.

## Phase 0 — Audit ✅ DONE
- [x] 7 audit docs (`docs/audit/`)
- [x] decisions log ADR-001..006 (`docs/audit/decisions.md`)
- [x] secret scan (no real `.env` tracked; manhwa PAT in `.git/config` flagged for LO rotation)

## Phase 1 — Monorepo foundation ✅ DONE
- [x] directory tree per LO structure (`apps/discord-bot`, `core/plugins`, module source slots)
- [x] ADR-006 runtime model (no Docker, systemd + Vercel, native pg/redis)
- [x] root `pyproject.toml` · `pnpm-workspace.yaml` · `.gitignore` · `Makefile`
- [x] `.env.example` (manhwa contract + pahe tuning)
- [x] `README.md`
- [x] CI `.github/workflows/ci.yml`
- [x] `deploy/` systemd units + Caddy + backup (no Docker)
- [x] API domain `media-backend.aldifhr.my.id`
- [x] smoke test green (4/4) + ruff clean
- [x] initial commit

## Phase 2 — Shared core infra ✅ DONE
- [x] core/config (pydantic-settings, manhwa env contract)
- [x] core/logging (StructuredLogger, kwargs-safe)
- [x] core/events (event envelope)
- [x] core/cache (redis JSON cache, graceful)
- [x] core/database (async engine + session + ping + migration discovery)
- [x] core/queue (arq on redis — one framework)
- [x] core/scheduler (redis distributed lock, fail-safe)
- [x] core/discord (Ed25519 verify + bot-API REST send)
- [x] core/plugins + Source Registry
- [x] core/notifications outbox (idempotent, SKIP LOCKED, backoff+jitter, DLQ) + platform schema
- [x] 23 tests green, ruff clean

## Phase 3 — Discord bot process ✅ DONE (process + inbound endpoint)
- [x] apps/discord_bot process (`python -m apps.discord_bot`, token-guarded, exits if token/key missing)
- [x] HTTP-interactions endpoint: Ed25519 verify, PING→PONG, command/component→defer ACK, 401 bad sig, 400 unknown
- [x] token isolated to this process only
- [ ] outbound `deliver_*` arq jobs consumed here — Phase 4
- [ ] slash-command handlers backed by domain data — Phase 5/6

## Phase 4 — Notification engine ✅ DONE
- [x] routing (resolve_targets + resolve_thread, pure)
- [x] engine (claim outbox -> resolve -> enqueue deliver -> history; enqueue-fail retries)
- [x] deliver job (token-isolated, in discord-bot) + delivery history + delivery-worker wiring
- [x] 40 tests green, ruff clean

## Phase 5 — Manhwa module 🚧 IN PROGRESS (moved ahead of media per LO)
- [x] vendor full manhwa-scanner backend into modules/manhwa (142 files, imports rewired)
- [x] API entrypoint (apps/api -> modules.manhwa.main:app)
- [x] dedup-semantics tests (fcfs/text/rating)
- [x] PROVEN running (isolated boot: API 200, /api/v1/interactive 401-verify, workers up)
- [x] media-backend.aldifhr.my.id LIVE via nginx -> :3000 (TLS ok)
- [x] CUTOVER DONE: platform-api(:3000)+platform-cron(:3001) run migrated manhwa; manhwa-api/cron stopped+disabled
- [x] fixed: recent_chapters re-exports batch_insert_recent_chapters (from _ingest) — pipeline completes (status ok, HEALTHY)

## Phase 6 — Media module (pahe) ✅ DONE
- [x] vendor pahe into modules/media (sources/pahe + common + scheduler + source_health)
- [x] 39 regression tests green; requests dep; ruff clean
- [x] PROVEN running (isolated boot: /pahe 200, scheduler scrapes)
- [x] CUTOVER DONE: platform-media(:3100) runs migrated pahe; pahe-scanner stopped+disabled; state migrated; all-scan.aldifhr.my.id live
## Phase 7 — Database consolidation ⬜
## Phase 8 — API + frontend compatibility ⬜
## Phase 9 — Integration + regression tests ⬜
## Phase 10 — Deployment prep ⬜
