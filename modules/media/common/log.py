"""Minimal structured logging.

The service used bare ``print()`` everywhere: no timestamps, no levels, and no
way to quiet a chatty module without editing it. This is a thin wrapper over
``logging`` that writes ``LEVEL timestamp logger message`` to stdout (so PM2
and the logrotate config keep working unchanged) and honours ``LOG_LEVEL``.

Deliberately small — no JSON formatter, no handlers to wire. The volume here
is a few lines per check, and stdout is already shipped to a file.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False

_FORMAT = "%(levelname)s %(asctime)s %(name)s %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def _level() -> int:
    raw = (os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO)


def configure() -> None:
    """Install the stdout handler once. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    root = logging.getLogger("media")
    root.handlers[:] = [handler]
    root.setLevel(_level())
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``media`` namespace."""
    configure()
    return logging.getLogger(f"media.{name}")
