"""platform-api entrypoint (``python -m apps.api``).

Phase 5 (manhwa-first): serves the migrated manhwa backend
(``modules.manhwa.main:app``). API + in-process cron worker + interactions
endpoint run together, exactly as the original manhwa-scanner did, so manhwa is
operational first. Decomposition into fully separate api/worker processes and
rewiring onto core/ (queue, notifications, unified bot) is a later refinement.
"""

from __future__ import annotations

import uvicorn

from modules.manhwa.main import app


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)


if __name__ == "__main__":
    main()
