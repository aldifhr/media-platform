"""Graceful shutdown — finish current job, close connections, exit cleanly."""
from __future__ import annotations

import signal
import sys
from typing import Callable, List

from modules.manhwa.logger import get_logger

logger = get_logger("shutdown")

_handlers: List[Callable[[], None]] = []
_shutdown_in_progress = False


def register_shutdown_handler(fn: Callable[[], None]) -> None:
    """Register a function to call on SIGTERM/SIGINT."""
    _handlers.append(fn)


def _shutdown(sig=None, frame=None):
    global _shutdown_in_progress
    if _shutdown_in_progress:
        logger.warn("shutdown already in progress, ignoring signal", signal=sig)
        return
    _shutdown_in_progress = True
    logger.info("shutdown signal received", signal=sig)
    
    for fn in _handlers:
        try:
            fn()
        except Exception as e:
            logger.warn("shutdown handler failed", err=str(e)[:120])
    
    logger.info("shutdown complete")
    sys.exit(0)


def install_signal_handlers() -> None:
    """Install SIGTERM/SIGINT handlers for graceful shutdown."""
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    logger.info("signal handlers installed")
