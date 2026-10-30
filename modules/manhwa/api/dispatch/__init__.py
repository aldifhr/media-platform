"""Failed dispatches API (formerly part of the compat layer).

Split into failed.py (list/retry/delete) and queue.py (queue depth). This
module keeps `router` and mounts the sub-routers so `from modules.manhwa.api.dispatch
import router` keeps working.
"""
from fastapi import APIRouter

router = APIRouter()

# /dispatch/undelivered lives in its own module: it is a different question
# from failed-dispatches (which is "sent and bounced") — this one is
# "eligible and never attempted at all", including chapters the 24h window
# dropped before the dispatcher ever saw them.
from modules.manhwa.api.dispatch.undelivered import router as _undelivered_router  # noqa: E402
from modules.manhwa.api.dispatch.failed import router as _failed_router  # noqa: E402
from modules.manhwa.api.dispatch.queue import router as _queue_router  # noqa: E402

router.include_router(_undelivered_router)
router.include_router(_failed_router)
router.include_router(_queue_router)

__all__ = ["router"]
