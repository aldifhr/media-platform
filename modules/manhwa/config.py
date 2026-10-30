"""Config loaded from .env (parity with be-ag shared/env.ts)."""
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    # Discord
    DISCORD_BOT_TOKEN: str = ""
    DISCORD_PUBLIC_KEY: str = ""
    DISCORD_CLIENT_SECRET: str = ""
    DISCORD_OAUTH_REDIRECT_URI: str = "https://scanner.aldifhr.my.id/api/auth/discord/callback"
    OUTBOUND_WEBHOOK_URLS: str = ""
    # Two-channel feed: RAW gets every new chapter (with an "Add to whitelist"
    # button), WHITELIST gets only whitelisted series. Channel ids in the same
    # order as guild_settings rows. Both optional — a channel set to "" is
    # simply never used, so single-channel setups keep working unchanged.
    RAW_DISCORD_CHANNEL_ID: str = ""
    WHITELIST_DISCORD_CHANNEL_ID: str = ""
    # Comma-separated role IDs allowed to press "Add to whitelist".
    # Empty = anyone in the channel (previous behaviour).
    WHITELIST_ROLE_IDS: str = ""
    DISCORD_GUILD_ID: str = ""
    DISCORD_APPLICATION_ID: str = ""
    ERROR_WEBHOOK_URL: str = ""
    ADMIN_REPORT_CHANNEL_ID: str = ""

    # Direct PostgreSQL connection (transaction pooler, IPv4, reachable from VPS)
    # Example: postgresql://postgres.<ref>:***@aws-1-ap-northeast-2.pooler.supabase.com:6543/postgres?sslmode=require
    DATABASE_URL: str = ""

    # Public base URL used by cron/embeds/fe to build absolute links
    # and proxy endpoints. Defaults to the deployed scanner host.
    PUBLIC_BASE_URL: str = "https://scanner.aldifhr.my.id"

    # Network
    API_PORT: int = 3000
    CRON_PORT: int = 3001

    # ══════════════════════════════════════════════════════════════════════════════
    # SOURCE DOMAINS — single source of truth. Change here + DB REPLACE once.
    # ══════════════════════════════════════════════════════════════════════════════

    SHINIGAMI_API_URL: str = ""
    SHINIGAMI_API_BASE: str = "https://api.shngm.io"
    SHINIGAMI_PUBLIC_BASE: str = "https://11.shinigami.asia"
    SHINIGAMI_PUBLIC_URL: str = ""
    SHINIGAMI_SERIES_PATH: str = "/series/"
    SHINIGAMI_CHAPTER_PATH: str = "/chapter/"

    IKIRU_API_BASE: str = "https://09.ikiru.wtf"
    IKIRU_PUBLIC_BASE: str = "https://09.ikiru.wtf"

    # Discord toggle — set false to run locally without bot / disable dispatch
    DISCORD_ENABLED: bool = True

    # Aliases (deprecated — see _sync_aliases)
    SECONDARY_SOURCE_URL: str = "https://api.shngm.io"
    SECONDARY_PUBLIC_BASE: str = "https://11.shinigami.asia"

    # All available sources. Active sources = SOURCE_KEYS - DISABLED_SOURCES.
    # ikiru must stay in this list: SOURCE_KEYS drives load_source_health_map
    # and save_source_health_map, so leaving it out meant ikiru never got a
    # source_health row at all — /sources/health showed only 2 of 3 sources,
    # and the consecutive_failures >= 3 auto-cooldown could never fire for it.
    SOURCE_KEYS: list[str] = ["shinigami", "ikiru"]
    # Comma-separated sources to skip in collection (ops toggle, no code change).
    DISABLED_SOURCES: str = ""

    @property
    def active_sources(self) -> list[str]:
        """Return SOURCE_KEYS minus DISABLED_SOURCES."""
        disabled = {s.strip().lower() for s in self.DISABLED_SOURCES.split(",") if s.strip()}
        return [s for s in self.SOURCE_KEYS if s not in disabled]

    # Scraper
    RSS_LOOKBACK_HOURS: int = 24

    # Cron
    CRON_SECRET: str = ""
    MONITOR_AUTH_TOKEN: str = ""
    # Falls back to MONITOR_AUTH_TOKEN if unset, so a single secret covers
    # both admin bearer auth and dashboard login.
    DASHBOARD_PASSWORD: str = ""
    # JWT session-cookie secret for /api/auth login.
    # Falls back to MONITOR_AUTH_TOKEN if unset; the boot guard only enforces
    # that *some* secret exists, not that this field is populated explicitly.
    AUTH_SECRET: str = ""
    # Kill switch: set "false" to halt all cron runs (returns 503, run skipped).
    # Used to pause cron without deleting code.
    CRON_ENABLED: str = "true"
    # Dev/test only: disable monitor auth entirely. NEVER set true in production.
    AUTH_DISABLED: bool = False
    # Whitelist auto-cleanup retention in days. 0 (default) DISABLES the daily
    # prune — a whitelist entry is a user subscription and persists until the
    # user removes it. Set >0 only if you want never-notified entries older
    # than N days dropped.
    WHITELIST_AUTO_CLEANUP_DAYS: int = 0

    # Redis (durable task queue for Discord add-to-whitelist jobs)
    REDIS_URL: str = "redis://localhost:6379/0"

    # HTTP
    HTTP_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # Image proxy: only these upstream hosts may be fetched. NO wildcards,
    # NO arbitrary ports — explicit host:port pairs to prevent SSRF.
    PROXY_ALLOWED_HOSTS: list[str] = [
        "g.shinigami.asia:443",
        "shinigami.asia:443",
        "assets.shngm.id:443",
        "09.ikiru.wtf:443",
        "cdn.ikiru.id:443",
    ]

    def get_proxy_hosts(self) -> list[str]:
        return sorted(set(self.PROXY_ALLOWED_HOSTS))

    # Deploy env: "production" | "development"
    ENVIRONMENT: str = "production"

    @model_validator(mode="after")
    def _sync_aliases(self):
        # Canonical: SHINIGAMI_API_URL / SHINIGAMI_PUBLIC_URL
        # Aliases (deprecated): SECONDARY_SOURCE_URL, SECONDARY_PUBLIC_BASE
        # Migration: 2026-09-09 → 2026-12-09 warn period, then remove alias branches + code refs.
        import os as _os

        def _warn(old: str, canonical: str):
            try:
                from modules.manhwa.logger import get_logger as _gl
                _gl("config").warn(f"DEPRECATED env alias {old} → use {canonical} (removal 2026-12-09)")
            except Exception:
                pass

        _has_old_api = bool(_os.environ.get("SECONDARY_SOURCE_URL"))
        _has_new_api = bool(_os.environ.get("SHINIGAMI_API_URL"))
        _has_old_pub = bool(_os.environ.get("SECONDARY_PUBLIC_BASE"))
        _has_new_pub = bool(_os.environ.get("SHINIGAMI_PUBLIC_URL"))

        # New names take precedence; sync both so code reading either still works during migration.
        if self.SHINIGAMI_API_URL:
            object.__setattr__(self, "SECONDARY_SOURCE_URL", self.SHINIGAMI_API_URL)
        elif self.SECONDARY_SOURCE_URL:
            _warn("SECONDARY_SOURCE_URL (alias)", "SHINIGAMI_API_URL")
            object.__setattr__(self, "SHINIGAMI_API_URL", self.SECONDARY_SOURCE_URL)
        if self.SHINIGAMI_PUBLIC_URL:
            object.__setattr__(self, "SECONDARY_PUBLIC_BASE", self.SHINIGAMI_PUBLIC_URL)
        elif self.SECONDARY_PUBLIC_BASE:
            _warn("SECONDARY_PUBLIC_BASE (alias)", "SHINIGAMI_PUBLIC_URL")
            object.__setattr__(self, "SHINIGAMI_PUBLIC_URL", self.SECONDARY_PUBLIC_BASE)
        return self

    @model_validator(mode="after")
    def _apply_fallback_secrets(self):
        # Single-secret mode: if MONITOR_AUTH_TOKEN is set but AUTH_SECRET or
        # DASHBOARD_PASSWORD is empty, fall back so the operator only needs to
        # set one secret in production.
        master = self.MONITOR_AUTH_TOKEN or ""
        if master:
            if not getattr(self, "AUTH_SECRET", ""):
                object.__setattr__(self, "AUTH_SECRET", master)
            if not getattr(self, "DASHBOARD_PASSWORD", ""):
                object.__setattr__(self, "DASHBOARD_PASSWORD", master)
        return self

    # M4 FIX: Warn on unrecognized env vars (extra="ignore" silently drops typos)
    # NOTE: This is a no-op placeholder — pydantic-settings doesn't support
    # "warn" mode natively. Documented for future migration to extra="forbid" in dev.
    # To detect typos, run: python3 -c "from modules.manhwa.config import settings; print(settings.model_dump())"

# Module-level constants (not Settings fields) — import from here to avoid Pydantic "non-annotated attribute" errors
VALID_SOURCES = ("shinigami", "ikiru")
VALID_SOURCES_WITH_ALL = ("shinigami", "ikiru", "all")
CRON_ACTIONS = (
    "update", "rss-fetch", "dispatch", "health",
    "rss-fetch:shinigami",
    "rss-fetch:ikiru",
    "enrich", "enrich-missing", "enrich-refresh", "failed-retry",
)

settings = Settings()


def disabled_sources() -> set[str]:
    """Sources turned off via DISABLED_SOURCES — the single parser.

    `settings.DISABLED_SOURCES` already reads a real process env var: pydantic-
    settings resolves env vars before .env, so a module-level `os.getenv` first
    is dead code, not a fallback. Every consumer goes through here so the semantics cannot drift.
    """
    raw = (settings.DISABLED_SOURCES or "").strip().lower()
    return {s.strip() for s in raw.split(",") if s.strip()}

def _validate_settings(s: "Settings") -> None:
    """Refuse to boot if running in production without required secrets.

    Prevents the silent 'auth disabled because .env not loaded' footgun:
    check_monitor_auth() returns True when MONITOR_AUTH_TOKEN is empty, so a
    missing secret would expose every protected endpoint.
    """
    # P0 #3: AUTH_DISABLED in production - warn and enforce auth (not hard fail, was causing 503 on VPS where .env has AUTH_DISABLED=true)
    if s.AUTH_DISABLED and s.ENVIRONMENT.lower() == "production":
        from modules.manhwa.logger import get_logger as _gl
        _gl("config").warn("BOOT GUARD: AUTH_DISABLED=true in production - auth enforced, not bypassed (set ENVIRONMENT=development to bypass)")
    if s.ENVIRONMENT.lower() != "production":
        return
    missing = []
    if not s.CRON_SECRET:
        missing.append("CRON_SECRET")
    if not s.MONITOR_AUTH_TOKEN:
        missing.append("MONITOR_AUTH_TOKEN")
    # AUTH_SECRET and DASHBOARD_PASSWORD fall back to MONITOR_AUTH_TOKEN,
    # so only MONITOR_AUTH_TOKEN is mandatory here.
    if not s.DATABASE_URL:
        missing.append("DATABASE_URL")
    if not s.DISCORD_BOT_TOKEN:
        missing.append("DISCORD_BOT_TOKEN")
    if not s.DASHBOARD_PASSWORD and not s.MONITOR_AUTH_TOKEN:
        missing.append("DASHBOARD_PASSWORD")
    if not s.AUTH_SECRET and not s.MONITOR_AUTH_TOKEN:
        missing.append("AUTH_SECRET")
    if not s.DISCORD_ENABLED:
        missing = [m for m in missing if m != "DISCORD_BOT_TOKEN"]
    if missing:
        raise RuntimeError(
            "BOOT GUARD: production environment missing required secrets: "
            + ", ".join(missing)
            + ". Set them in .env or set ENVIRONMENT=development to bypass."
        )

_validate_settings(settings)
