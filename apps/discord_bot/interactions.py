"""HTTP-interactions endpoint (Ed25519-verified) for the Discord bot.

Inbound command/button routing per ADR-001.
"""
from __future__ import annotations

import json
import os

from fastapi import FastAPI, Request, Response

from core.discord import verify_interaction
from core.logging import get_logger

log = get_logger("discord:http")
# Discord interaction/response types
PING = 1
PONG = 1
APPLICATION_COMMAND = 2
MESSAGE_COMPONENT = 3
CHANNEL_MESSAGE_WITH_SOURCE = 4
UPDATE_MESSAGE = 7
DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5

# Authorized user ID for exclude button (zerosense)
# Set in .env as EXCLUDE_AUTHORIZED_USER_ID for easy updates without code changes.
_AUTHORIZED_EXCLUDE_USER = os.environ.get("EXCLUDE_AUTHORIZED_USER_ID", "656952310")


def create_app() -> FastAPI:
    app = FastAPI(title="platform-discord")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.post("/interactions")
    async def interactions(request: Request):
        raw = await request.body()
        sig = request.headers.get("X-Signature-Ed25519", "")
        ts = request.headers.get("X-Signature-Timestamp", "")
        if not verify_interaction(raw, sig, ts):
            return Response(status_code=401)

        data = json.loads(raw)
        itype = data.get("type")

        if itype == PING:
            return {"type": PONG}

        if itype == MESSAGE_COMPONENT:
            custom_id = (data.get("data") or {}).get("custom_id", "")
            user_id = str((data.get("member") or data.get("user") or {}).get("id", ""))
            guild_id = str(data.get("guild_id", ""))
            channel_id = str(data.get("channel_id", ""))
            message_id = str(data.get("message", {}).get("id", ""))

            if custom_id.startswith("exclude:"):
                if user_id != _AUTHORIZED_EXCLUDE_USER:
                    return {
                        "type": CHANNEL_MESSAGE_WITH_SOURCE,
                        "data": {
                            "content": "Only the operator can use this button.",
                            "flags": 1 << 6,  # EPHEMERAL
                        },
                    }

                try:
                    _, source, slug = custom_id.split(":", 2)
                    title_key = slug
                except ValueError:
                    return {"type": UPDATE_MESSAGE, "data": {"components": []}}

                try:
                    from modules.manhwa.db import q
                    q(
                        """INSERT INTO public.exclude_list (guild_id, title_key, source, user_id)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT (guild_id, title_key, source) DO NOTHING""",
                        [guild_id, title_key, source, user_id],
                    )
                except Exception as exc:
                    log.warning("exclude: db insert failed", err=str(exc)[:160])

                return {
                    "type": UPDATE_MESSAGE,
                    "data": {
                        "content": "Excluded — future chapters will not appear in the raw feed.",
                        "components": [
                            {
                                "type": 1,
                                "components": [
                                    {
                                        "type": 2,
                                        "style": 3,
                                        "label": "Add to whitelist",
                                        "custom_id": f"wladd:{source}:{slug}",
                                        "disabled": True,
                                    },
                                    {
                                        "type": 2,
                                        "style": 4,
                                        "label": "Excluded",
                                        "custom_id": custom_id,
                                        "disabled": True,
                                    },
                                ],
                            }
                        ],
                    },
                }

            if custom_id.startswith("wladd:"):
                return {"type": DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE}

            log.info("interaction received", type=itype, custom_id=custom_id[:40])
            return {"type": DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE}

        if itype == APPLICATION_COMMAND:
            name = (data.get("data") or {}).get("name", "")
            log.info("interaction received", type=itype, name=name)
            return {"type": DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE}

        return Response(status_code=400)

    return app
