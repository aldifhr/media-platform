# Source Inventory — Phase 0 Audit

Audit date: 2026-10-30 · Every source verified against actual code + git history, not README claims.

## Manhwa module (from manhwa-scanner)

| Source | Adapter | Collector | Status | Notes |
|---|---|---|---|---|
| ikiru | `app/scrapers/ikiru/` | `app/cron/collectors/ikiru.py` | ACTIVE | primary; cookie auth |
| shinigami | (collector only) | `app/cron/collectors/shinigami.py` | ACTIVE | second live origin |

Removed and confirmed absent from active code (per user profile + git): voratoon, komiku, kiryuu, wurmz. Historical migration references remain but no live adapter. `source list = ["shinigami", "ikiru"]`.

Domain logic to preserve (all present in code): gap-fill strategy, whitelist, chapter normalization, FCFS deduplication (`core/fcfs.py`), 24h rolling feed, series metadata enrichment (`cron/enrich`, `series_meta_sync`), dispatch history, dispatch claims, failed-dispatch retry, RSS API, catalog API, reader API, image proxy (`api/cover.py`, `core/ssrf.py`), dashboard snapshot, source health monitoring.

## Media module (from media-scrapers)

| Source | Status | Evidence |
|---|---|---|
| pahe | ACTIVE | `pahe/scanner/{service,rules,scraper,notify,translate}.py`; only source in tree |
| korea | DELETED | commit `4c11ede remove dead scanners: korea, drakorasia, game3rb, ovagames, skidrow, zeigames, vlr` |
| drakorasia | DELETED | same commit (was in git history: 25 file-touches) |
| game3rb | DELETED | same commit (8 touches) |
| ovagames | DELETED | same commit (10 touches) |
| skidrow | DELETED | same commit (14 touches) |
| zeigames | DELETED | same commit (8 touches) |
| vlr | DELETED | same commit |

**Critical finding:** the master prompt lists 7 media sources, but only **pahe** exists in the current repository. The other six were removed in a single commit and are NOT present in the working tree. They are not "silently removed" by this migration — they were already deleted upstream and are documented here with their removal commit. Migrating them would require resurrecting dead code from git history, which contradicts "do not invent source functionality" and "verify each source is actually active."

**Recommendation:** migrate pahe only. Treat the other six as archived (git history is the rollback/reference point). If any must return, that is a new build task, not a migration.

## pahe source detail

- Fetch/parse: `scraper.py` (feed + post-page), `service.py` orchestrates scrape -> filter -> notify -> mark-seen.
- Freshness rule: `rules.py` — one definition, two callers. Movies gated by `PAHE_MIN_YEAR` (prod 2026); serials always notify (year=None or Season/Episode in title). Freshness is the post id, not release year (pahe back-fills old films).
- Seen-state: `common/state.py` atomic file JSON (`data/pahe_seen.json`).
- Health: `source_health.py` consecutive-failure tracking + auto-quarantine.
- Delivery: `notify.py` builds embeds, delivers via bot REST; thread routing rules (year>=2026 -> `PAHE_THREAD_2026`, horror -> `PAHE_THREAD_HORROR`, first match wins).
- Translate: `translate.py` MyMemory backend + disk cache (`data/translated.json`), overridable via `TRANSLATE_URL`.

## Source abstraction gap

media-scrapers has no formal source registry — pahe is hard-wired into `media.py`/`scheduler.py`. manhwa has per-collector modules but no unified registry either. The target `core` Source Registry (id, category, enabled, fetch, normalize, health, timeout, retry, last-success, error-stats, cleanup) is a **new** abstraction to build in Phase 2, wrapping — not rewriting — each existing scraper.
