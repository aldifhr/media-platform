"""Direct PostgreSQL access layer (replaces PostgREST/Supabase client).

This module is now a thin facade over app/db/ (adapter, query_builder, raw).
It is kept so the ~40 existing `from modules.manhwa.db_adapter import ...` call sites keep
working unchanged.

Backend connects to Supabase via the TRANSACTION POOLER (IPv4, reachable from
the VPS) using psycopg2. The builder-style API
(`get_supabase().table(...).select(...).eq(...)`) mirrors the PostgREST surface
the rest of the codebase uses, so call sites do NOT need rewriting.

Connection: DATABASE_URL env (transaction pooler, sslmode=require).
"""
from __future__ import annotations

from modules.manhwa.db.adapter import (  # noqa: F401
    DATABASE_URL,
    close_pool,
    get_conn,
    get_pool_stats,
    put_conn,
)
from modules.manhwa.db.query_builder import get_supabase  # noqa: F401
from modules.manhwa.db.raw import q  # noqa: F401

__all__ = [
    "get_supabase",
    "get_conn",
    "put_conn",
    "q",
    "close_pool",
    "get_pool_stats",
    "DATABASE_URL",
]
