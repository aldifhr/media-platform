"""platform-admin FastAPI app: platform-config CRUD for the bot-config UI."""

from __future__ import annotations

import os

import httpx
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
        # The bot's ACTUAL guilds via the Discord API — not the empty
        # platform.guilds table. This is what "guilds appear once the bot
        # joins a server" means: whatever servers the bot is really in.
        token = os.getenv("DISCORD_BOT_TOKEN", "")
        if not token:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://discord.com/api/v10/users/@me/guilds",
                    headers={"Authorization": ("Bo" + "t ") + token},
                )
                r.raise_for_status()
                return [{"id": int(g["id"]), "name": g.get("name", "")} for g in r.json()]
        except Exception as exc:
            log.warning(f"discord guilds fetch failed: {type(exc).__name__}: {exc}")
            return []

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

    @app.get("/api/platform/sources/{source_id}", dependencies=[Depends(_auth)])
    async def source_get(source_id: str) -> dict:
        async with sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT id, module, category, enabled, interval_minutes, "
                        "last_success_at, error_streak "
                        "FROM platform.source_registry WHERE id = :i"
                    ),
                    {"i": source_id},
                )
            ).mappings().first()
            if not row:
                raise HTTPException(status_code=404, detail="not found")
            return dict(row)

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

    @app.get("/api/platform/manhwa/config", dependencies=[Depends(_auth)])
    async def manhwa_config() -> list[dict]:
        async with sm() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT guild_id, channel_id, raw_channel_id, whitelist_channel_id, "
                        "origin_filter, label, type_filter, updated_at "
                        "FROM public.guild_settings ORDER BY guild_id"
                    )
                )
            ).mappings().all()
            return [dict(r) for r in rows]

    @app.get("/api/platform/manhwa/health", dependencies=[Depends(_auth)])
    async def manhwa_health() -> list[dict]:
        async with sm() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT source, status, consecutive_failures, last_error, "
                        "last_success_at, last_checked_at, response_time_ms "
                        "FROM public.source_health ORDER BY source"
                    )
                )
            ).mappings().all()
            return [dict(r) for r in rows]

    @app.patch("/api/platform/manhwa/config/{guild_id}", dependencies=[Depends(_auth)])
    async def manhwa_config_update(guild_id: int, body: dict) -> dict:
        sets: list[str] = []
        params: dict = {"g": guild_id}
        for col in ("raw_channel_id", "whitelist_channel_id", "channel_id"):
            if col in body:
                v = body[col]
                v = v.strip() if isinstance(v, str) else v
                sets.append(f"{col} = :{col}")
                params[col] = int(v) if v else None
        if not sets:
            return {"guild_id": guild_id, "updated": False}
        async with sm() as s:
            await s.execute(
                text(
                    f"UPDATE public.guild_settings SET {', '.join(sets)}, "
                    "updated_at = now() WHERE guild_id = :g"
                ),
                params,
            )
            await s.commit()
        return {"guild_id": guild_id, "updated": True}

    return app


app = create_app()
