"""Notification engine: durable outbox -> routing -> enqueue delivery.

The engine runs in the worker process (no bot token). It claims pending outbox
rows, resolves targets + thread, and enqueues ``deliver_message`` onto the arq
queue — which is consumed by the discord-bot process (the only token holder).
Idempotent on event_id; enqueue failure re-queues the row with backoff.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from core import queue
from core.events import Event
from core.notifications import routing
from core.notifications.outbox import OutboxRepo


@dataclass
class DeliveryIntent:
    event_id: str
    guild_id: int
    channel_id: int
    thread_id: int | None
    content: str
    embeds: list


def _row_to_event(row: dict) -> Event:
    payload = row.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    occurred = row["occurred_at"]
    occurred_str = occurred.isoformat() if hasattr(occurred, "isoformat") else str(occurred)
    return Event(
        event_id=str(row["event_id"]),
        event_type=row["event_type"],
        version=int(row.get("version", 1)),
        source=row["source"],
        occurred_at=occurred_str,
        payload=payload or {},
    )


def default_formatter(event: Event) -> tuple[str, list]:
    """Placeholder formatter; modules register richer per-event-type formatters."""
    return f"**{event.event_type}**\n{json.dumps(event.payload, ensure_ascii=False)}", []


def plan_deliveries(
    rows: list[dict],
    subscriptions: list[dict],
    routes: list[dict],
    formatter=default_formatter,
) -> list[DeliveryIntent]:
    intents: list[DeliveryIntent] = []
    for row in rows:
        event = _row_to_event(row)
        content, embeds = formatter(event)
        thread_id = routing.resolve_thread(routes, event)
        for t in routing.resolve_targets(subscriptions, event):
            intents.append(
                DeliveryIntent(
                    event_id=event.event_id,
                    guild_id=int(t["guild_id"]),
                    channel_id=int(t["channel_id"]),
                    thread_id=int(thread_id) if thread_id else None,
                    content=content,
                    embeds=embeds,
                )
            )
    return intents


class NotificationEngine:
    def __init__(self, repo: OutboxRepo | None = None) -> None:
        self.repo = repo or OutboxRepo()

    async def process_batch(
        self,
        session,
        subscriptions: list[dict],
        routes: list[dict],
        formatter=default_formatter,
        limit: int = 50,
    ) -> int:
        rows = await self.repo.claim_batch(session, limit)
        if not rows:
            return 0

        by_event: dict[str, list[DeliveryIntent]] = {}
        for intent in plan_deliveries(rows, subscriptions, routes, formatter):
            by_event.setdefault(intent.event_id, []).append(intent)

        failed_events: set[str] = set()
        for event_id, intents in by_event.items():
            for intent in intents:
                try:
                    await queue.enqueue(
                        "deliver_message",
                        event_id=intent.event_id,
                        guild_id=intent.guild_id,
                        channel_id=intent.channel_id,
                        thread_id=intent.thread_id,
                        content=intent.content,
                        embeds=intent.embeds,
                    )
                except Exception:  # noqa: BLE001
                    failed_events.add(event_id)

        for row in rows:
            eid = str(row["event_id"])
            if eid in failed_events:
                await self.repo.mark_failed(
                    session, row["id"], "enqueue failed", row["attempts"] + 1, row["max_attempts"]
                )
            else:
                await self.repo.mark_delivered(session, row["id"])

        await session.commit()
        return sum(len(v) for k, v in by_event.items() if k not in failed_events)
