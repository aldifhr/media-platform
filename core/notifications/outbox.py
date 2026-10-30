"""Durable notification outbox (PostgreSQL transactional outbox).

Scraper commits an Event in the SAME transaction as its domain write, then the
notification engine claims pending rows (FOR UPDATE SKIP LOCKED), routes, and
delivers. Idempotent on event_id. Retry with exponential backoff + jitter;
dead-letter at max_attempts. Redis Pub/Sub is NOT used for durability.
"""

from __future__ import annotations

import json
import random

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import Event


def compute_backoff(attempt: int, base: float = 5.0, cap: float = 3600.0) -> float:
    """Full-jitter exponential backoff in seconds for the given attempt number."""
    exp = min(cap, base * (2 ** max(0, attempt - 1)))
    return random.uniform(0.0, exp)


class OutboxRepo:
    async def enqueue(self, session: AsyncSession, event: Event) -> None:
        await session.execute(
            text(
                """
                INSERT INTO platform.notification_outbox
                  (event_id, event_type, version, source, occurred_at, payload)
                VALUES
                  (:event_id, :event_type, :version, :source, :occurred_at,
                   CAST(:payload AS jsonb))
                ON CONFLICT (event_id) DO NOTHING
                """
            ),
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "version": event.version,
                "source": event.source,
                "occurred_at": event.occurred_at,
                "payload": json.dumps(event.payload, ensure_ascii=False),
            },
        )

    async def claim_batch(self, session: AsyncSession, limit: int = 50) -> list[dict]:
        rows = (
            (
                await session.execute(
                    text(
                        """
                    SELECT id, event_id, event_type, version, source, occurred_at,
                           payload, attempts, max_attempts
                    FROM platform.notification_outbox
                    WHERE status = 'pending' AND next_attempt_at <= now()
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT :limit
                    """
                    ),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )
        ids = [r["id"] for r in rows]
        if ids:
            await session.execute(
                text(
                    "UPDATE platform.notification_outbox "
                    "SET status = 'claimed', updated_at = now() WHERE id = ANY(:ids)"
                ),
                {"ids": ids},
            )
        return [dict(r) for r in rows]

    async def mark_delivered(self, session: AsyncSession, row_id: int) -> None:
        await session.execute(
            text(
                "UPDATE platform.notification_outbox "
                "SET status = 'delivered', updated_at = now() WHERE id = :id"
            ),
            {"id": row_id},
        )

    async def mark_failed(
        self, session: AsyncSession, row_id: int, error: str, attempts: int, max_attempts: int
    ) -> None:
        err = (error or "")[:500]
        if attempts >= max_attempts:
            await session.execute(
                text(
                    "UPDATE platform.notification_outbox "
                    "SET status = 'dead', last_error = :err, updated_at = now() WHERE id = :id"
                ),
                {"id": row_id, "err": err},
            )
            return
        delay = compute_backoff(attempts)
        await session.execute(
            text(
                """
                UPDATE platform.notification_outbox
                SET status = 'pending', attempts = :attempts, last_error = :err,
                    next_attempt_at = now() + make_interval(secs => :delay),
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": row_id, "err": err, "attempts": attempts, "delay": delay},
        )
