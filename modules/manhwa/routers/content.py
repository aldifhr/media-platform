"""Content routers — rss/public/ws/whitelist/queue + audit."""
from modules.manhwa.api import public_stats as public_stats_api, audit as audit_api, recommended as recommended_api
from modules.manhwa.api.rss.custom import router as rss_custom_api_router


def register_content(app):
    app.include_router(rss_custom_api_router, prefix="/api/v1")
    app.include_router(public_stats_api.router, prefix="/api/v1")
    app.include_router(audit_api.router, prefix="/api/v1")
    app.include_router(recommended_api.router, prefix="/api/v1")
