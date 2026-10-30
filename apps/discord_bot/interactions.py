"""HTTP-interactions endpoint (Ed25519-verified) for the Discord bot.

Inbound command/button routing per ADR-001. Command handlers that need domain
data are wired in later phases; for now commands ACK (defer) so Discord never
times out. Delivery of notifications is outbound (Phase 4), not here.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request, Response

from core.discord import verify_interaction
from core.logging import get_logger

log = get_logger("discord:http")

# Discord interaction/response types
PING = 1
PONG = 1
APPLICATION_COMMAND = 2
MESSAGE_COMPONENT = 3
DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5


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

        if itype in (APPLICATION_COMMAND, MESSAGE_COMPONENT):
            name = (data.get("data") or {}).get("name", "")
            log.info("interaction received", type=itype, name=name)
            # ACK now; real handlers run async and edit the deferred response.
            return {"type": DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE}

        return Response(status_code=400)

    return app
