# Discord Inventory — Phase 0 Audit

Audit date: 2026-10-30 · Two very different Discord integration models exist today.

## manhwa-scanner — full inbound + outbound

### Inbound (commands / interactions)
- Endpoint: `POST` interactions route in `main.py:298`, handler `app/discord/router.py::handle_interaction`.
- Verification: **Ed25519** signature via `discord_interactions.verify_key` (`client.py::verify_interaction` + `verify_interaction_v2`). This is the HTTP-interactions model, NOT a discord.py gateway bot.
- Handles `APPLICATION_COMMAND` (2) and `MESSAGE_COMPONENT` (button rows). Component interactions carry `member.roles` for permission checks (`router.py:220`).
- Command logic referenced as "parity with api/interactive.ts + lib/commands/*" — command definitions live partly in the frontend/shared layer.
- PING/ACK + deferred response constants defined in `router.py`.

### Outbound (delivery)
- `app/discord/client.py`: primary path is bot **REST** (`POST /channels/{id}/messages`), with 429 handling in `_discord_request`. 400/403/404 are treated as permanent (no retry, no gateway fallback). Timeout/unknown -> gateway fallback.
- `app/discord/gateway_sender.py`: **gateway websocket** fallback when REST is IP-blocked. Cannot carry components/file attachments.
- File attachments: REST-with-files first, then gateway (no files).
- `app/discord/embeds.py`: embed construction.
- Delivery engine: `app/services/dispatch/` — `channels` (routing), `backfill`, `runner`, `retry`, `service`, `senders`. Thread unarchive-before-send (HEAD commit `aba1c4b`).
- Guild config: `guild_settings` table; multi-guild supported (`018_guild_settings_multi`, `077_guild_feed_channels`).

## media-scrapers — outbound only

- No inbound command handling (verified: no `application_command`/`verify_key`/Ed25519 in app code).
- Delivery: `common/discord.py` + `pahe/scanner/notify.py` — bot **REST** `POST /channels/{id}/messages`. Deliberately NOT webhooks (webhooks 404 forever after channel recreation; a bot token + channel id survives).
- Thread routing: `notify.py` ordered rules, first match wins — release year >= 2026 -> `PAHE_THREAD_2026`; genre horror -> `PAHE_THREAD_HORROR`; else `PAHE_DISCORD_CHANNEL`.
- Single channel/thread target; no per-guild subscription model (single-guild by config).

## Token/secret surface (do not commit; rotate if leaked)

- manhwa: Discord bot token (REST + gateway), interaction public key (Ed25519 verify). Guild/channel IDs in `guild_settings`.
- media: `PAHE_DISCORD_TOKEN`, `PAHE_DISCORD_CHANNEL`, `PAHE_THREAD_2026`, `PAHE_THREAD_HORROR`, `PAHE_API_TOKEN`, `MEDIA_API_TOKEN`.
- **Git-history audit required** (Phase 1): both repos' history must be scanned for committed tokens/webhooks. manhwa remote URL currently embeds a PAT (`ghp_...`) — that credential is exposed in `.git/config` and must be rotated + removed from any persisted config.

## Target unified bot (Phase 3) — decision required

Target wants ONE discord.py bot with Cogs + slash command groups (`/manhwa`, `/media`, `/notification`, `/admin`, `/utility`). Two viable paths:

- **A — Keep HTTP-interactions model.** manhwa already verifies Ed25519 and routes commands/components; extend the router with the new command groups. No gateway dependency for commands; delivery keeps REST+gateway fallback. Lower risk, preserves working code. discord.py becomes optional.
- **B — Migrate to discord.py gateway bot.** Matches the prompt's letter (Cogs, `discord.py 2.x`), but replaces a working, signature-verified command path and adds a persistent gateway connection the scrapers must not touch.

**DECISION (LO, 2026-10-30): A confirmed.** Both the manhwa and media (pahe) modules use the existing HTTP-interactions model (Ed25519 verify) — pahe delivery adopts the same pattern as manhwa-scanner. Bot token isolated to the `platform-discord` process only (workers/scrapers never see it). Documented deviation from "discord.py Cogs": the Ed25519 interactions endpoint is production-proven and avoids standing up a second gateway. discord.py becomes optional, not required.
