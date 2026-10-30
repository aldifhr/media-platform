"""platform-admin entrypoint (``python -m apps.admin``)."""

from __future__ import annotations

import uvicorn

from apps.admin.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8002, log_config=None)


if __name__ == "__main__":
    main()
