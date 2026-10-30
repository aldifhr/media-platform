# Architecture Decisions (ADR log) — media-platform

Running record of LO's architectural calls. Newest last. Each is binding unless LO revises it.

## ADR-001 — Discord model: HTTP-interactions, not discord.py gateway
**Date:** 2026-10-30 · **Status:** Accepted
Both the manhwa and media (pahe) modules use the existing HTTP-interactions model (Ed25519 `verify_key`), the same pattern manhwa-scanner already runs in production. Inbound slash/button commands via the verified interactions endpoint; outbound delivery via bot REST + gateway-websocket fallback. discord.py becomes optional, not required. Bot token is isolated to the `platform-discord` process only — workers/scrapers never see it.
*Deviation from prompt:* prompt named discord.py Cogs; we keep the proven Ed25519 endpoint and avoid a second gateway.

## ADR-002 — Media module scope: pahe only
**Date:** 2026-10-30 · **Status:** Accepted
Migrate pahe only. korea/drakorasia/skidrow/ovagames/zeigames/game3rb were deleted upstream (commit `4c11ede`) and are archived in git history as the reference/rollback point. No resurrection of dead code.

## ADR-003 — pahe delivery: Discord bot API only (no webhook)
**Date:** 2026-10-30 · **Status:** Accepted
pahe migrates fully onto the Discord bot (bot API `POST /channels/{id}/messages`), same transport as manhwa. The webhook transport (`common/discord.py::send_webhook`) is dropped for the media module — webhooks 404 forever after channel recreation. Thread routing (release-year / genre rules) is preserved but delivered via the bot to threads.

## ADR-004 — Shared config/env: manhwa-scanner contract
**Date:** 2026-10-30 · **Status:** Accepted
The platform uses manhwa-scanner's env/config contract because it is the component already on the Discord bot: `DISCORD_BOT_TOKEN`, `MONITOR_AUTH_TOKEN` (single-secret mode covering dashboard/auth/OPS session), guild settings in Postgres. Do NOT introduce `PAHE_DISCORD_TOKEN` / `PAHE_DISCORD_CHANNEL` style env naming for delivery — pahe's target channel becomes a DB-driven guild subscription + notification route, not an env var. pahe-specific tuning (min-year, allowed tags, interval, thread ids) stays as source config.

## ADR-005 — Web UI surfaces
**Date:** 2026-10-30 · **Status:** Accepted
Three distinct surfaces:
- **manhwa reader** = existing `apps/frontend` (Next.js 16). Preserved, not redesigned during migration.
- **media-platform bot-configuration UI** = its own admin surface for configuring the bot: guilds, per-guild settings, notification channels/routes, subscriptions, source enable/disable. Implemented by extending the **ops dashboard** (`apps/ops`) — YAGNI: one admin surface rather than a fourth frontend. Reversible to a dedicated app if it outgrows ops.
- **pahe** = no web UI.

## ADR-006 — Runtime & deployment: no Docker, systemd + Vercel
**Date:** 2026-10-30 · **Status:** Accepted
- **No Docker.** Nothing is containerized.
- **Backend** runs natively on the VPS as three systemd units — `platform-api`, `platform-discord`, `platform-worker` — logging to journald.
- **Frontends** (manhwa reader + media-platform bot-config/ops UI) deploy on **Vercel**.
- **Postgres + Redis** run natively on the VPS (not containerized).
- Caddy reverse proxy in front of the backend; health checks; log rotation via journald; backup + rollback documented.

## Open questions (not yet decided)
- Alembic vs. keep the custom SQL migration runner (Phase 7).
- Exact queue framework (arq recommended) — Phase 2.
- Whether bot-config UI eventually splits from ops — revisit if ops gets crowded.
