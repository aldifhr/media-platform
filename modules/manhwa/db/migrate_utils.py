"""Pure, DB-free helpers for the startup migration runner (app/main.py lifespan).

Kept side-effect-free so they can be unit-tested without a live Postgres. The
runner imports these; nothing here touches a connection.
"""
from __future__ import annotations

import os
import re

_NUM_PREFIX = re.compile(r"^(\d+)")
_CONCURRENTLY = re.compile(r"\bconcurrently\b", re.IGNORECASE)
_TIMEOUT_OK = re.compile(r"^\d+(ms|s|min|h)?$")


def migration_sort_key(filename: str) -> tuple[int, str]:
    """Order migration files by their leading integer, then by name.

    Lexical sort is wrong: as strings '100_x.sql' < '98_x.sql', so 100 would
    run before 98/99. Sorting by the numeric prefix keeps 001..099..100 in the
    intended order. Files with no leading number sort last, stably by name.
    """
    m = _NUM_PREFIX.match(filename)
    num = int(m.group(1)) if m else 10**9
    return (num, filename)


def is_concurrent_sql(sql: str) -> bool:
    """True if the SQL uses CONCURRENTLY (CREATE INDEX / REFRESH ... CONCURRENTLY).

    Such statements cannot run inside a transaction block, so the runner must
    execute these files on an autocommit connection instead of the default
    transactional one.
    """
    return bool(_CONCURRENTLY.search(sql))


def migration_statement_timeout(default: str = "900s") -> str:
    """Return a validated statement_timeout literal for a SET statement.

    Reads MIGRATION_STATEMENT_TIMEOUT; falls back to ``default`` when unset or
    malformed. Validated against a strict pattern so it is safe to inline into
    ``SET statement_timeout = '<value>'`` (Postgres SET takes no bind param).
    """
    raw = (os.getenv("MIGRATION_STATEMENT_TIMEOUT") or "").strip()
    if raw and _TIMEOUT_OK.match(raw):
        return raw
    return default
