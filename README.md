# media-platform

Unified media platform — modular monolith consolidating **manhwa-scanner** (manhwa tracker + reader) and **media-scrapers** (pahe release notifier). One Discord bot, one shared backend, independent API / Discord / Worker processes.

> Status: **Phase 1 (monorepo foundation)**. Live checklist: [`docs/phase-status.md`](docs/phase-status.md). Audit + decisions: [`docs/audit/`](docs/audit/).

## Architecture

```
apps/
  api/           FastAPI unified API (reads: reader, catalog, RSS, dashboard, bot-config)
  discord-bot/   Single Discord bot — HTTP-interactions (Ed25519) + delivery. Token isolated here.
  worker/        scheduler + scrapers + notification engine
  frontend/      manhwa reader (Next.js 16) — preserved
  ops/           ops dashboard + bot-config UI (guilds/subscriptions/routes/sources)
modules/
  manhwa/        sources (ikiru, shinigami) · services · commands
  media/         sources (pahe) · services · commands
  future/        extension slot
core/            discord notifications scheduler queue database cache logging config plugins
packages/shared/ shared TS types + OpenAPI client
migrations/      platform manhwa media
```

## Key decisions (see docs/audit/decisions.md)
- **Discord:** HTTP-interactions model (Ed25519 verify), not discord.py gateway. Both modules. Token only in `platform-discord`.
- **Media sources:** pahe only (others deleted upstream, archived in git history).
- **pahe delivery:** Discord bot API only, no webhook.
- **Config:** manhwa-scanner env contract (`DISCORD_BOT_TOKEN`, `MONITOR_AUTH_TOKEN`).
- **Runtime:** no Docker. Backend = VPS systemd (journald); frontends = Vercel; Postgres + Redis native.

## Reference / rollback (source repos, untouched)
- media-scrapers @ `4c481bb`
- manhwa-scanner @ `aba1c4b`

## Dev
```bash
make install   # uv sync
make lint      # ruff
make test      # pytest
make smoke     # layout smoke test
```

## Deploy
Backend: systemd units in [`deploy/systemd/`](deploy/systemd/) (`platform-api`, `platform-discord`, `platform-worker`). API domain: `media-backend.aldifhr.my.id`. Frontends: Vercel. Proxy: Caddy ([`deploy/caddy/`](deploy/caddy/)). Backups: [`deploy/scripts/backup.sh`](deploy/scripts/backup.sh).
