"""Health service facade — re-exports storage health reads for the API layer.

Keeps api/ importing from services/ (layering) while the SQL stays in storage.
"""
from modules.manhwa.storage.health import (  # noqa: F401
    load_source_health_map,
    read_dashboard_snapshot,
)
