"""Database layer — pool/adapter, query builder, raw SQL helper.

`app.db_adapter` remains the public facade re-exporting these names so the
~40 existing `from modules.manhwa.db import ...` / `from modules.manhwa.db_adapter import ...` call
sites keep working unchanged.

Submodules:
  * `adapter.py`       — connection pool + get_conn/put_conn/close_pool
  * `query_builder.py` — PostgREST-style builder (get_supabase)
  * `raw.py`           — q() raw parameterized SQL
"""
from modules.manhwa.db.adapter import (  # noqa: F401
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
]
