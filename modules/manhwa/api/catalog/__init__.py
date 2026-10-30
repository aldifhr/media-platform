"""Catalog package — re-exports list + chapters + badge + item routers."""
from fastapi import APIRouter
from modules.manhwa.api.catalog.list import router as list_router
from modules.manhwa.api.catalog.chapters import router as chapters_router
from modules.manhwa.api.catalog.badge import router as badge_router
from modules.manhwa.api.catalog.item import router as item_router

router = APIRouter()
router.include_router(list_router)
router.include_router(chapters_router)
router.include_router(badge_router)
router.include_router(item_router)
