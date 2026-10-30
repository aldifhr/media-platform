# Deploy — media-platform

Runtime model (ADR-006): **no Docker**. Backend runs as native systemd units on the VPS; frontends deploy on Vercel; Postgres + Redis run natively. **The VPS reverse proxy is nginx** (Caddy is installed but not running — deviation from the original plan; we match the box's existing infra).

## Live now
- `media-backend.aldifhr.my.id` → nginx → `127.0.0.1:3000` (running manhwa backend). Cert: Let's Encrypt (auto-renew). Config: `nginx/media-backend.conf`. `/api/v1/interactive` verifies (401 unsigned), `/openapi.json` 200.

## Layout
- `systemd/platform-{api,discord,worker}.service` — unit templates (entrypoints land in Phase 2+).
- `caddy/Caddyfile.snippet` — reverse-proxy snippet.
- `scripts/backup.sh` — Postgres pg_dump backup with retention.

## Install (VPS)
```bash
# 1. code + env
cd /root/projects/media-platform
cp .env.example .env   # fill secrets; never commit
uv sync

# 2. units (systemd cannot be written directly to /etc/systemd/system by tooling)
cp deploy/systemd/platform-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now platform-api platform-discord platform-worker

# 3. logs
journalctl -u platform-api -f
```

## Frontends (Vercel)
`apps/frontend` (reader) and `apps/ops` (bot-config UI) deploy on Vercel. Env: `BACKEND_URL`, `MONITOR_AUTH_TOKEN`, `OPS_LOGIN_PASSWORD` (see `.env.example`).

## Rollback
Source repos are untouched and pinned in `README.md` (media-scrapers `4c481bb`, manhwa-scanner `aba1c4b`). DB rollback: restore latest `deploy/scripts/backup.sh` dump.
