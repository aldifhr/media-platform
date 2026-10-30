"""Logger — JSON lines with correlation, severity gate, file sinks, DB journal.

Ceiling: queue unbounded in-memory (burst 1k logs ok). Upgrade: BoundedQueue+drop when OOM.
"""
from __future__ import annotations
import json
import sys
import os
import traceback
import threading
import logging
import logging.handlers
import re
import queue
from datetime import datetime, timezone

_local = threading.local()
def set_correlation_id(cid: str | None) -> None: _local.cid = cid
def get_correlation_id() -> str | None: return getattr(_local, "cid", None)

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").strip().lower()
if _LOG_LEVEL not in _LEVELS: _LOG_LEVEL = "info"
_THRESHOLD = _LEVELS[_LOG_LEVEL]

def _make_file_handler(path: str, level: int):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        h = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        h.setLevel(level); h.setFormatter(logging.Formatter("%(message)s")); return h
    except Exception: return None

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FULL_PATH = os.environ.get("LOG_FILE", os.path.join(_ROOT, "logs", "be-ag-py.log"))
_ERR_PATH = os.environ.get("LOG_ERROR_FILE", os.path.join(_ROOT, "logs", "error.log"))
_file_handler = _make_file_handler(_FULL_PATH, 10)
_err_handler = _make_file_handler(_ERR_PATH, 40)
_file_logger = logging.getLogger("be-ag-py-file")
_file_logger.setLevel(logging.DEBUG); _file_logger.propagate = False
if _file_handler: _file_logger.addHandler(_file_handler)
if _err_handler: _file_logger.addHandler(_err_handler)

_TOKEN_RE = re.compile(r"(token|authorization|bearer)\s*[=:]\s*[^\s&\"']+", re.IGNORECASE)
def _clean(s) -> str:
    txt = str(s).replace("\n", " ").replace("\r", " ")
    txt = _TOKEN_RE.sub(lambda m: m.group(0).split("=",1)[0]+"=***" if "=" in m.group(0) else m.group(0).split(":",1)[0]+": ***" if ":" in m.group(0) else "***", txt)
    txt = re.sub(r"Bearer\s+[A-Za-z0-9_\-]{20,}", "Bearer ***", txt, flags=re.IGNORECASE)
    return txt

_q: queue.Queue = queue.Queue()

# Error sink — set by the app at startup to persist warn/error rows to the DB.
# Kept as an injected callable so logger has no upward import (it would drag
# app.storage into every module that logs, breaking the core-is-leaf layering).
_error_sink = None
def set_error_sink(fn) -> None:
    global _error_sink
    _error_sink = fn

def _worker():
    while True:
        try:
            level, scope, msg, stack, path, cid, meta = _q.get()
            try:
                if _error_sink is not None:
                    src_field = (meta or {}).get("source")
                    row_meta = dict(meta or {})
                    row_meta["scope"] = scope
                    _error_sink(level=level, source=src_field or scope, message=msg[:2000], stack=stack, path=path, correlation_id=cid, meta=row_meta)
            except Exception: pass
            _q.task_done()
        except Exception: pass
threading.Thread(target=_worker, daemon=True).start()

class Logger:
    def __init__(self, scope: str = "app"): self.scope = scope
    def _emit(self, level: str, msg: str, exc: BaseException | None = None, **fields):
        if _LEVELS.get(level, 20) < _THRESHOLD: return
        rec = {"level": level, "time": datetime.now(timezone.utc).isoformat(), "scope": self.scope, "msg": _clean(msg)}
        cid = get_correlation_id()
        if cid: rec["cid"] = cid
        if exc is not None:
            rec["error_type"] = type(exc).__name__
            rec["error_msg"] = _clean(str(exc))
            rec["traceback"] = _clean("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        if fields:
            for k, v in fields.items(): rec[k] = _clean(v) if isinstance(v, str) else v
        line = json.dumps(rec, default=str)
        sys.stdout.write(line + "\n"); sys.stdout.flush()
        if _file_handler or _err_handler: _file_logger.log(_LEVELS[level], line)
        if level in ("error", "warn"):
            stack = rec.get("traceback")
            _q.put((level, self.scope, rec.get("msg",""), stack, rec.get("path") or fields.get("path"), cid, {k: v for k, v in rec.items() if k not in ("level","time","scope","msg","cid","traceback","error_type","error_msg")}))
    def debug(self, msg="", **fields): self._emit("debug", msg, **fields)
    def info(self, msg="", **fields): self._emit("info", msg, **fields)
    def warn(self, msg="", **fields): self._emit("warn", msg, **fields)
    def warning(self, msg="", **fields): self.warn(msg, **fields)
    def error(self, msg="", exc: BaseException | None = None, **fields):
        if exc is None and "err" in fields and isinstance(fields["err"], BaseException): exc = fields.pop("err")
        self._emit("error", msg, exc=exc, **fields)

def get_logger(scope: str = "app") -> Logger: return Logger(scope)
