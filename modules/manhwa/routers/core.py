"""Core routers — health/admin/catalog/auth/dispatch."""
from modules.manhwa.api import catalog as catalog_api, auth as auth_api, dispatch as dispatch_api, system as system_api, rss as rss_api, health as health_api, cover as cover_api, cron as cron_api, error_logs as logs_api, continue_reading as continue_reading_api
from modules.manhwa.api.admin import whitelist as admin_whitelist_api, stats as admin_stats_api, queue as admin_queue_api
from modules.manhwa.api.admin.series import router as admin_series_api_router


def register_core(app):
    app.include_router(health_api.router, prefix="/api/v1")
    app.include_router(admin_series_api_router, prefix="/api/v1")
    app.include_router(admin_whitelist_api.router, prefix="/api/v1")
    app.include_router(admin_stats_api.router, prefix="/api/v1")
    app.include_router(catalog_api.router, prefix="/api/v1")
    app.include_router(auth_api.router, prefix="/api/v1")
    app.include_router(dispatch_api.router, prefix="/api/v1")
    app.include_router(cover_api.router, prefix="/api/v1")
    app.include_router(rss_api.router, prefix="/api/v1")
    app.include_router(cron_api.router, prefix="/api/v1")
    app.include_router(logs_api.router, prefix="/api/v1")
    app.include_router(admin_queue_api.router, prefix="/api/v1")
    app.include_router(system_api.router, prefix="/api/v1")
    app.include_router(continue_reading_api.router, prefix="/api/v1")
