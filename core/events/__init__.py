"""Standardized event envelope for the notification pipeline.

Scraper -> normalize -> Event -> outbox -> notification engine -> routing -> delivery.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

EVENT_VERSION = 1

# Example event types:
#   manhwa.chapter.released
#   media.release.detected


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    version: int = EVENT_VERSION
    source: str
    occurred_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict = Field(default_factory=dict)


def new_event(event_type: str, source: str, payload: dict, version: int = EVENT_VERSION) -> Event:
    return Event(event_type=event_type, source=source, payload=payload, version=version)
