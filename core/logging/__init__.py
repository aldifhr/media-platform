"""Structured JSON logging to stdout (journald-friendly).

get_logger returns a StructuredLogger whose methods accept arbitrary keyword
fields: log.debug("msg", key=..., err=...). Fields ride on the LogRecord via
``extra`` and are merged into the JSON line. This mirrors manhwa-scanner's
structured-logger ergonomics without pulling in a logging dependency.
"""

from __future__ import annotations

import json
import logging
import sys

from core.config import get_settings

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class StructuredLogger:
    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _emit(self, level: int, msg: str, fields: dict) -> None:
        self._logger.log(level, msg, extra={"fields": fields})

    def debug(self, msg: str, **fields) -> None:
        self._emit(logging.DEBUG, msg, fields)

    def info(self, msg: str, **fields) -> None:
        self._emit(logging.INFO, msg, fields)

    def warn(self, msg: str, **fields) -> None:
        self._emit(logging.WARNING, msg, fields)

    warning = warn

    def error(self, msg: str, **fields) -> None:
        self._emit(logging.ERROR, msg, fields)


def _configure() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(get_settings().log_level.upper())
    _configured = True


def get_logger(name: str) -> StructuredLogger:
    _configure()
    return StructuredLogger(name)
