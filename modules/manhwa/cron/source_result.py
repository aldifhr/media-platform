from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True)
class SourceResult:
    source: str
    items: list[dict]
    fetched_at: datetime
    latency_ms: int
    pages_fetched: int = 0
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    @classmethod
    def ok(cls, source: str, items: list[dict], started: float, pages_fetched: int = 0):
        import time
        return cls(source, items, datetime.now(timezone.utc), int((time.monotonic() - started) * 1000), pages_fetched)

    @classmethod
    def failed(cls, source: str, started: float, error: Exception):
        import time
        return cls(source, [], datetime.now(timezone.utc), int((time.monotonic() - started) * 1000), error=str(error))
