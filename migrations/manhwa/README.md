# Manhwa schema history

These are manhwa-scanner's raw SQL migrations (reference commit `aba1c4b`),
preserved as the source of truth for the manhwa schema. They target the
`public` schema as originally written.

Phase 7 (database consolidation) baselines the **live** schema into the
`manhwa` schema of the shared platform DB, preserving PKs, unique constraints,
indexes, dedup history, and timestamps. Do not hand-edit historical files;
add new numbered migrations for changes.
