"""Manhwa module: tracker (ikiru, shinigami) + reader domain.

Migrated from manhwa-scanner (reference commit ``aba1c4b``). Business logic is
preserved verbatim where possible; shared concerns (config, db, cache, queue,
scheduler, discord, notifications) map onto ``core/``. Schema history lives in
migrations/manhwa/ and is baselined into the ``manhwa`` schema in Phase 7.

Deviation from LO's simplified tree (sources/services/commands): ``domain/``,
``repositories/``, ``jobs/``, and ``api/`` are restored from the original spec
— the manhwa domain needs DB repositories, cron jobs, and reader/catalog/RSS
route handlers that don't fit under sources/services/commands alone.
"""

MODULE_NAME = "manhwa"
SOURCES = ["ikiru", "shinigami"]
