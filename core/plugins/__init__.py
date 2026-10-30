"""Plugin + Source Registry.

A standard source abstraction (ADR: Source Registry). Sources register here;
the worker/scheduler iterates the registry. REST and HTML sources keep their
own internals — the registry only standardizes metadata + lifecycle hooks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from core.logging import get_logger

log = get_logger("core:plugins")


@dataclass
class SourceMeta:
    id: str
    category: str  # "manhwa" | "media"
    enabled: bool = True
    timeout: float = 30.0
    retry: int = 3
    interval_minutes: int = 30


class Source(ABC):
    meta: SourceMeta

    @abstractmethod
    async def fetch(self):
        """Return raw items from the source."""

    @abstractmethod
    def normalize(self, raw):
        """Normalize a raw item into a domain object."""

    async def health_check(self) -> bool:
        return True

    async def cleanup(self) -> None:  # optional lifecycle
        return None


class SourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, Source] = {}

    def register(self, source: Source) -> None:
        self._sources[source.meta.id] = source

    def get(self, source_id: str) -> Source | None:
        return self._sources.get(source_id)

    def all(self) -> list[Source]:
        return list(self._sources.values())

    def enabled(self, category: str | None = None) -> list[Source]:
        return [
            s
            for s in self._sources.values()
            if s.meta.enabled and (category is None or s.meta.category == category)
        ]


registry = SourceRegistry()


def source(meta: SourceMeta):
    """Class decorator: build + register a Source subclass."""

    def wrap(cls: type[Source]) -> type[Source]:
        cls.meta = meta
        registry.register(cls())
        return cls

    return wrap
