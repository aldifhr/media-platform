# Risk Assessment — Phase 0 Audit

Audit date: 2026-10-30 · Ranked by blast radius. Each risk has a mitigation tied to a phase.

## HIGH

**R1 — Six of seven media sources do not exist.**
The prompt lists korea/drakorasia/pahe/skidrow/ovagames/zeigames/game3rb, but only pahe is in the repo; the other six were deleted (commit `4c11ede`). Migrating "all active sources" therefore means pahe only.
*Mitigation:* **CONFIRMED (LO, 2026-10-30):** migrate pahe only; the six are archived (git history = reference). (source-inventory.md)

**R2 — Exposed credentials in git history / config.**
manhwa remote URL embeds a GitHub PAT (`ghp_...`) in `.git/config`; both repos may carry Discord tokens/webhooks in history. media `.env` + `.env.bak.*` exist on disk.
*Mitigation:* Phase 1 secret scan; rotate PAT + any Discord token/webhook; scrub `.git/config`; never echo secrets (redact in all outputs). Confirm nothing sensitive is committed before the monorepo's first push.

**R3 — Discord model mismatch.**
Target says discord.py Cogs; manhwa uses a working Ed25519 HTTP-interactions endpoint + REST/gateway delivery. A blind swap to discord.py replaces proven code and adds a gateway the scrapers must not touch.
*Mitigation:* **RESOLVED (LO, 2026-10-30):** keep HTTP-interactions model for both manhwa + pahe; extend the router. Token isolated to `platform-discord`. Deviation documented. (discord-inventory.md)

**R4 — Single-process -> multi-process split.**
Today API + cron worker + Discord share one uvicorn process. Splitting into api/discord/worker changes scheduling, locking, and delivery lifetimes.
*Mitigation:* lift the existing lock patterns (advisory lock, `cron_locks`, `FOR UPDATE SKIP LOCKED`) into `core`; single scheduler process; integration tests for "scheduler runs once," "worker crash doesn't kill other jobs," "restart doesn't lose jobs."

## MEDIUM

**R5 — Notification durability.**
No generic outbox today; dispatch is DB-backed but there's no standardized event envelope or dead-letter. Redis is cache-only, so "Redis Pub/Sub" is explicitly insufficient.
*Mitigation:* Phase 4 transactional outbox (`notification_outbox`) + envelope + retry/backoff/jitter + DLQ + delivery history. Test Discord-down and restart-recovery.

**R6 — pahe file-state -> shared DB.**
Seen-state is `data/*.json` (single-host, atomic-file). Moving to Postgres changes durability + multi-worker semantics.
*Mitigation:* Phase 5 one-time import + file fallback during cutover; regression-test freshness + dedup so behavior is identical before removing the file path.

**R7 — Migration tooling divergence.**
manhwa uses a custom SQL runner (87 files, non-sequential numbers, some duplicates); target stack says Alembic.
*Mitigation:* Phase 7 decide: adopt Alembic going forward (baseline from live schema) OR keep SQL runner and document deviation. Snapshot live schema first; never edit an already-applied migration — add a new numbered one.

**R8 — Dedup semantics are load-bearing.**
FCFS dedup, chapter normalization/ordering, composite uniques, and the pahe freshness rule are subtle and back-fill-aware.
*Mitigation:* no change without tests demonstrating correctness; lock current behavior with regression tests before refactors (Phase 9).

## LOW

**R9 — Frontend/ops coupling.** Reader + ops dashboards depend on exact API contracts.
*Mitigation:* Phase 8 compatibility routes + contract tests; no frontend redesign during migration.

**R10 — Scope creep to a rewrite.** The prompt is explicit: consolidation, not rewrite.
*Mitigation:* wrap, don't rewrite; every phase keeps a working system; deviations documented in `docs/audit/architecture-analysis.md`.

## Blocking gate
Do not begin Phase 5+ (any code migration) until R2 (secret rotation) is resolved. (R3 Discord model decided: HTTP-interactions, 2026-10-30.)
