"""Boot-time config validation — fail fast on missing critical settings."""
from __future__ import annotations

import os
from typing import List, Tuple

from modules.manhwa.logger import get_logger

logger = get_logger("boot:config")

# (env_var, required_in_prod, description)
#
# These mirror app.config._validate_settings — the single source of truth for
# the production secret check. AUTH_SECRET and DASHBOARD_PASSWORD are NOT
# listed: they fall back to MONITOR_AUTH_TOKEN (see _apply_fallback_secrets),
# so only the master secret is mandatory. Listing AUTH_SECRET here made the
# app refuse to boot in the single-secret deploy it was designed for.
CRITICAL_VARS: List[Tuple[str, bool, str]] = [
    ("DATABASE_URL", True, "Postgres connection string"),
    ("DISCORD_BOT_TOKEN", True, "Discord bot token for notifications"),
    ("MONITOR_AUTH_TOKEN", True, "Master secret (covers AUTH_SECRET/DASHBOARD_PASSWORD via fallback)"),
    ("CRON_SECRET", True, "Cron internal auth"),
]

def validate_settings() -> None:
    """Raise RuntimeError if critical config is missing in production."""
    # Conservative default: an unset ENVIRONMENT is treated as production, so
    # a missing env var fails the guard rather than silently running in dev
    # mode with auth relaxed. Matches app.config.Settings.ENVIRONMENT.
    env = os.getenv("ENVIRONMENT", "production").lower()
    is_prod = env == "production"

    missing = []
    for var, required_in_prod, desc in CRITICAL_VARS:
        val = os.getenv(var, "").strip()
        if not val:
            if required_in_prod and is_prod:
                missing.append(f"{var} ({desc})")
            elif required_in_prod:
                logger.warn(f"missing config (non-prod): {var} ({desc})")

    if missing:
        raise RuntimeError(
            f"BOOT GUARD: missing critical config: {', '.join(missing)}"
        )

    # Validate Redis URL format
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url and not redis_url.startswith("redis://"):
        logger.warn(f"REDIS_URL looks invalid: {redis_url[:40]}")

    logger.info("config validation ok", env=env)
