"""Platform settings.

Env contract follows manhwa-scanner (the component already on the Discord bot).
Real environment variables always win over the .env file (pydantic-settings
default precedence). Never commit real secrets.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Discord (bot API; token ONLY in the platform-discord process) ---
    discord_bot_token: str = ""
    discord_public_key: str = ""  # Ed25519 interactions verify key
    discord_gateway_enabled: bool = False  # gateway-websocket fallback

    # --- Platform auth (single-secret mode) ---
    monitor_auth_token: str = ""
    cron_secret: str = ""

    # --- Datastore (native on VPS, no Docker) ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/media_platform"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # --- Frontend ---
    backend_url: str = ""

    # --- pahe source tuning (media module) ---
    pahe_min_year: int = 2026
    pahe_allowed_tags: str = ""
    pahe_check_interval_minutes: int = 30
    pahe_thread_2026: str = ""
    pahe_thread_horror: str = ""
    translate_url: str = ""

    # --- Logging ---
    log_level: str = "INFO"

    @property
    def pahe_allowed_tag_set(self) -> set[str]:
        return {t.strip().lower() for t in self.pahe_allowed_tags.split(",") if t.strip()}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
