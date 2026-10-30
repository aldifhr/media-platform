"""platform-admin FastAPI app: platform-config CRUD for the bot-config UI."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import text

from core.config import get_settings
from core.database import get_sessionmaker
from core.logging import get_logger

log = get_logger("admin:api")


def _auth(request: Request) -> None:
    """Bearer auth against MONITOR_AUTH_TOKEN. Disabled when unset (dev)."""
    tok = get_settings().monitor_auth_token
    if not tok:
        return
    if request.headers.get("Authorization", "") != f"Bearer {tok}":
        raise HTTPException(status_code=401, detail="unauthorized")


def create_app() -> FastAPI:
    app = FastAPI(title="platform-admin")
    sm = get_sessionmaker()

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/platform/guilds", dependencies=[Depends(_auth)])
    async def guilds() -> list[dict]:
        async with sm() as s:
            rows = (
                await s.execute(
                    text("SELECT id, name, created_at FROM platform.guilds ORDER BY id")
                )
            ).mappings().all()
            return [dict(r) for r in rows]

    @app.get("/api/platform/subscriptions", dependencies=[Depends(_auth)])
    async def subscriptions() -> list[dict]:
        async with sm() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT id, guild_id, module, category, channel_id, enabled, created_at "
                        "FROM platform.subscriptions ORDER BY id"
                    )
                )
            ).mappings().all()
            return [dict(r) for r in rows]

    @app.post("/api/platform/subscriptions", dependencies=[Depends(_auth)])
    async def subscription_create(body: dict) -> dict:
        async with sm() as s:
            await s.execute(
                text(
                    "INSERT INTO platform.guilds (id, name) VALUES (:g, :n) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"g": int(body["guild_id"]), "n": body.get("guild_name", "")},
            )
            new_id = (
                await s.execute(
                    text(
                        """
                        INSERT INTO platform.subscriptions
                          (guild_id, module, category, channel_id, enabled)
                        VALUES (:g, :m, :c, :ch, :e)
                        ON CONFLICT (guild_id, module, category, channel_id)
                          DO UPDATE SET enabled = :e
                        RETURNING id
                        """
                    ),
                    {
                        "g": int(body["guild_id"]),
                        "m": body["module"],
                        "c": body.get("category"),
                        "ch": int(body["channel_id"]),
                        "e": bool(body.get("enabled", True)),
                    },
                )
            ).scalar()
            await s.commit()
            return {"id": new_id}

    @app.delete("/api/platform/subscriptions/{sub_id}", dependencies=[Depends(_auth)])
    async def subscription_delete(sub_id: int) -> dict:
        async with sm() as s:
            await s.execute(
                text("DELETE FROM platform.subscriptions WHERE id = :i"), {"i": sub_id}
            )
            await s.commit()
            return {"deleted": sub_id}

    @app.get("/api/platform/routes", dependencies=[Depends(_auth)])
    async def routes() -> list[dict]:
        async with sm() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT id, module, event_type, rule, priority, "
                        "channel_id, thread_id, enabled "
                        "FROM platform.notification_routes ORDER BY priority, id"
                    )
                )
            ).mappings().all()
            return [dict(r) for r in rows]

    @app.post("/api/platform/routes", dependencies=[Depends(_auth)])
    async def route_create(body: dict) -> dict:
        async with sm() as s:
            new_id = (
                await s.execute(
                    text(
                        """
                        INSERT INTO platform.notification_routes
                          (module, event_type, rule, priority, channel_id, thread_id, enabled)
                        VALUES (:m, :et, CAST(:rule AS jsonb), :p, :ch, :th, :e)
                        RETURNING id
                        """
                    ),
                    {
                        "m": body["module"],
                        "et": body["event_type"],
                        "rule": body.get("rule", "{}"),
                        "p": int(body.get("priority", 100)),
                        "ch": body.get("channel_id"),
                        "th": body.get("thread_id"),
                        "e": bool(body.get("enabled", True)),
                    },
                )
            ).scalar()
            await s.commit()
            return {"id": new_id}

    @app.get("/api/platform/sources", dependencies=[Depends(_auth)])
    async def sources() -> list[dict]:
        async with sm() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT id, module, category, enabled, interval_minutes, "
                        "last_success_at, error_streak "
                        "FROM platform.source_registry ORDER BY id"
                    )
                )
            ).mappings().all()
            return [dict(r) for r in rows]

    @app.patch("/api/platform/sources/{source_id}", dependencies=[Depends(_auth)])
    async def source_update(source_id: str, body: dict) -> dict:
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE platform.source_registry SET enabled = :e, "
                    "updated_at = now() WHERE id = :i"
                ),
                {"e": bool(body.get("enabled", True)), "i": source_id},
            )
            await s.commit()
            return {"id": source_id, "enabled": bool(body.get("enabled", True))}

    @app.get("/api/platform/outbox/stats", dependencies=[Depends(_auth)])
    async def outbox_stats() -> dict:
        async with sm() as s:
            rows = (
                await s.execute(
                    text(
                    "SELECT status, count(*) AS n "
                        "FROM platform.notification_outbox GROUP BY status"
                    )
                )
            ).mappings().all()
            return {r["status"]: int(r["n"]) for r in rows}

    return app


app = create_app()
