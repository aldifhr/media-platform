"""Task queue package — split from monolithic tasks.py.

Modules:
  queue      — Redis queue client + enqueue helpers
  scheduler  — internal cron scheduler (interval-based enqueue)
  retention  — hourly DB prune loop
  lifecycle  — thread start/stop (start_worker, stop_worker, run_cron_worker)
"""

from modules.manhwa.tasks.queue import (
    enqueue_cron,
    enqueue_add,
    _get_redis,
    QUEUE_KEY,
    DLQ_KEY,
    CRON_QUEUE_KEY,
    CRON_QUEUE_SET,
    QUEUE_PROCESSING_KEY,
    CRON_PROCESSING_KEY,
)
from modules.manhwa.tasks.scheduler import start_cron_scheduler
from modules.manhwa.tasks.lifecycle import start_worker, stop_worker, run_cron_worker, get_cron_status

__all__ = [
    "enqueue_cron",
    "enqueue_add",
    "_get_redis",
    "get_cron_status",
    "QUEUE_KEY",
    "DLQ_KEY",
    "CRON_QUEUE_KEY",
    "CRON_QUEUE_SET",
    "QUEUE_PROCESSING_KEY",
    "CRON_PROCESSING_KEY",
    "start_cron_scheduler",
    "start_worker",
    "stop_worker",
    "run_cron_worker",
]
