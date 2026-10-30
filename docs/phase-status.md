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

## Phase 2 — Shared core infra 🚧 IN PROGRESS
core/config, database, cache, queue (arq), scheduler, events, notifications, discord, plugins, Source Registry.

## Phase 3 — Discord bot process ⬜
HTTP-interactions endpoint + delivery; token isolated to `platform-discord`.

## Phase 4 — Notification engine ⬜
Transactional outbox, event envelope, retry/backoff/jitter, DLQ, delivery history.

## Phase 5 — Media module (pahe) ⬜
## Phase 6 — Manhwa module ⬜
## Phase 7 — Database consolidation ⬜
## Phase 8 — API + frontend compatibility ⬜
## Phase 9 — Integration + regression tests ⬜
## Phase 10 — Deployment prep ⬜
