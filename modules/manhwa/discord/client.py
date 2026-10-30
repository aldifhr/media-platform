"""Discord interaction crypto + HTTP sender (parity with lib/discord/*)."""
from __future__ import annotations

import json
import random
import time
import httpx
from discord_interactions import verify_key

from modules.manhwa.config import settings
from modules.manhwa.logger import get_logger
from modules.manhwa.core.resilience import cb_discord

logger = get_logger("discord")

_HEADERS = {"User-Agent": "DiscordBot (https://github.com/aldifhr/be-ag-py, 1.0)"}

class _TimeoutSentinel:
    """Distinct marker for a Discord send whose HTTP result is unknown."""
    _timeout = True

# Shared httpx client for Discord API (avoids socket churn)
_discord_client: httpx.Client | None = None

def _get_discord_client() -> httpx.Client:
    global _discord_client
    if _discord_client is None:
        _discord_client = httpx.Client(timeout=10, headers=_HEADERS)
    return _discord_client

def close_discord_client() -> None:
    global _discord_client
    if _discord_client is not None:
        _discord_client.close()
        _discord_client = None

def verify_interaction(raw_body: bytes, signature: str, timestamp: str) -> bool:
    """Verify Ed25519 signature from Discord."""
    if not settings.DISCORD_PUBLIC_KEY:
        logger.warn("DISCORD_PUBLIC_KEY not set — refusing")
        return False
    try:
        logger.info("verify_interaction", sig_len=len(signature), ts_len=len(timestamp), body_len=len(raw_body))
        result = verify_key(
            raw_body,
            signature,
            timestamp,
            settings.DISCORD_PUBLIC_KEY,
        )
        if not result:
            logger.warn("verify_interaction returned False", sig_len=len(signature))
        return result
    except Exception as e:
        # Without these the line is undiagnosable: sig_len/ts_len say whether the
        # headers arrived at all, and pk_format says whether the stored key is the
        # hex verify_key() needs or the base64url the Discord Portal displays.
        logger.error(
            "verify_interaction error",
            exc=e,
            exc_type=type(e).__name__,
            sig_len=len(signature or ""),
            ts_len=len(timestamp or ""),
            body_len=len(raw_body or b""),
            pk_format="hex" if _is_hex(settings.DISCORD_PUBLIC_KEY) else "non-hex",
        )
        return False

def _is_hex(value: str) -> bool:
    """True when every character is a hex digit and the length is even."""
    if not value:
        return False
    try:
        bytes.fromhex(value)
        return True
    except ValueError:
        return False


def _decode_discord_public_key(pk: str) -> bytes:
    """Decode Discord public key — handles both hex and base64url formats."""
    try:
        # Try hex first (what discord_interactions.verify_key expects)
        decoded = bytes.fromhex(pk)
        if len(decoded) == 32:
            return decoded
    except ValueError:
        pass
    # Try base64url (what Discord Portal actually shows)
    import base64
    try:
        decoded = base64.urlsafe_b64decode(pk + "==")
        if len(decoded) == 32:
            return decoded
    except Exception:
        pass
    raise ValueError(f"Unable to decode Discord public key (len={len(pk)})")

def verify_interaction_v2(raw_body: bytes, signature: str, timestamp: str) -> bool:
    """Verify Ed25519 signature from Discord — handles base64url PK format."""
    if not settings.DISCORD_PUBLIC_KEY:
        logger.warn("DISCORD_PUBLIC_KEY not set — refusing")
        return False
    try:
        pk_bytes = _decode_discord_public_key(settings.DISCORD_PUBLIC_KEY)
        from nacl.signing import VerifyKey
        vk = VerifyKey(pk_bytes)
        # Discord sends signature as hex (64 bytes = 128 hex chars)
        sig_bytes = bytes.fromhex(signature)
        vk.verify(timestamp.encode() + raw_body, sig_bytes)
        logger.info("verify_interaction OK", sig_len=len(signature))
        return True
    except Exception as e:
        # Same diagnostics as verify_interaction -- the two failure modes here are
        # a non-hex signature header and a malformed public key, and the bare
        # exception name distinguishes neither.
        logger.error(
            "verify_interaction error",
            exc=e,
            exc_type=type(e).__name__,
            sig_len=len(signature or ""),
            pk_decoded_ok=_safe_decode_pk() is not None,
        )
        return False

def _safe_decode_pk():
    try:
        return _decode_discord_public_key(settings.DISCORD_PUBLIC_KEY)
    except Exception:
        return None


def _discord_request(method: str, url: str, *, json_data: dict | None = None, files: dict | None = None, max_retries: int = 3) -> httpx.Response | None:
    """Send a Discord API request with 429 + Retry-After handling.

    D1 FIX: Reads Retry-After header on 429, uses exponential backoff with jitter.
    Circuit-aware: fast-fails when discord CB is OPEN.

    → jangan retry timeout (httpx.TimeoutException) — return None (unknown) biar caller tidak double-send
    """
    if not cb_discord.allow():
        logger.warn("discord circuit OPEN — dropping request", method=method)
        return None
    headers = {**_HEADERS, "Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}"}
    client = _get_discord_client()

    for attempt in range(max_retries + 1):
        try:
            if files is not None:
                r = client.request(method, url, files=files, headers=headers)
            elif json_data is not None:
                r = client.request(method, url, json=json_data, headers=headers)
            else:
                r = client.request(method, url, headers=headers)

            if r.status_code < 400:
                cb_discord.record_success()
                return r
            if r.status_code == 429:
                retry_after = r.headers.get("retry-after")
                if retry_after:
                    try:
                        wait = float(retry_after)
                    except (ValueError, TypeError):
                        wait = min(2 ** attempt, 30) + random.uniform(0, 1.0)
                else:
                    wait = min(2 ** attempt, 30) + random.uniform(0, 1.0)
                logger.warn("Discord 429 rate limited", wait=round(wait, 2), attempt=attempt)
                time.sleep(wait)
                continue
            # 4xx (except 429) is a definitive response — the circuit breaker
            # should not treat it as a failure, but it must record the outcome
            # so a half-open probe does not leak.
            if r.status_code < 500:
                cb_discord.record_success()
            else:
                cb_discord.record_failure()
            return r
        except Exception as e:
            msg = str(e).lower()
            is_timeout = isinstance(e, httpx.TimeoutException) or "timeout" in msg or "timed out" in msg or "readtimeout" in msg
            cb_discord.record_failure()
            logger.error("discord request failed", method=method, err=str(e), timeout=is_timeout)
            if is_timeout:
                return _TimeoutSentinel()
            if attempt < max_retries:
                time.sleep(min(2 ** attempt, 10) + random.uniform(0, 1.0))
                continue
    cb_discord.record_failure()
    return None


# Discord thread channel types: GuildNewsThread=10, GuildPublicThread=11,
# GuildPrivateThread=12. Dispatch can target threads (raw/whitelist feeds);
# a thread auto-archives after idle, and posting to an archived thread fails,
# so we unarchive before sending.
_THREAD_TYPES = {10, 11, 12}
# Classified once via GET, then cached: avoids a GET on every send to a
# regular channel (the common case).
_THREAD_CHANNELS: set[str] = set()
_NON_THREAD_CHANNELS: set[str] = set()


def _ensure_thread_postable(channel_id: str) -> None:
    """Best-effort: make a thread channel able to receive a message.

    For a thread we PATCH ``archived=false`` before the send (idempotent —
    unarchiving an active thread is a no-op) so an auto-archived thread still
    accepts the message. Regular channels are classified once and skipped
    thereafter. Every failure is swallowed: this is an optimization, the send
    is still attempted regardless.
    """
    cid = str(channel_id)
    if cid in _NON_THREAD_CHANNELS:
        return
    try:
        if cid not in _THREAD_CHANNELS:
            r = _discord_request("GET", f"https://discord.com/api/v10/channels/{cid}")
            if r is None or r.status_code != 200:
                return
            if r.json().get("type") in _THREAD_TYPES:
                _THREAD_CHANNELS.add(cid)
            else:
                _NON_THREAD_CHANNELS.add(cid)
                return
        _discord_request(
            "PATCH",
            f"https://discord.com/api/v10/channels/{cid}",
            json_data={"archived": False},
        )
    except Exception:
        pass


def send_channel_message(channel_id: str, content: str | None = None, embeds: list | None = None, components: list | None = None):
    """Send a message to a channel via bot token.

    Primary path: Discord REST API. If that fails (e.g. VPS IP banned at
    the REST layer — Cloudflare 1010 / Discord 40333), fall back to the
    gateway websocket sender.

    `components` (button rows) only travel on the REST path. The gateway
    fallback cannot carry components, so a component-bearing send that lands
    on the fallback returns None instead of posting a message stripped of its
    button — the caller treats None as a failed send and retries, which is the
    correct outcome for a button whose absence makes the message useless.
    """
    try:
        _ensure_thread_postable(channel_id)
        r = _discord_request("POST", f"https://discord.com/api/v10/channels/{channel_id}/messages", json_data=_build_payload(content, embeds, components))
        if isinstance(r, _TimeoutSentinel):
            return r
        if r is not None and r.status_code < 400:
            return r.json()
        if r is not None and r.status_code not in (400, 403, 404):
            # 429 sudah di-handle di _discord_request, 5xx sudah return r (bukan None) → jangan gateway
            if r.status_code >= 500 or r.status_code == 429:
                return None
        # 400/403/404 banned → jangan gateway
        if r is None:
            # None = timeout/unknown → jangan gateway (avoid duplicate), return None
            return None
    except Exception as e:
        logger.warn("send_channel_message REST failed, trying gateway", channel=channel_id, err=str(e)[:120])
        # exception unknown → jangan gateway
        return None

    # Fallback: gateway websocket (not IP-banned like REST)
    if components:
        logger.warn("send_channel_message: gateway fallback cannot carry components, skipping", channel=channel_id)
        return None
    try:
        from modules.manhwa.discord.gateway_sender import send_via_gateway
        if send_via_gateway(channel_id, content=content, embeds=embeds):
            return {"id": "gateway", "channel_id": channel_id}
    except Exception as e:
        logger.error("send_channel_message gateway fallback failed", channel=channel_id, err=str(e)[:160])
    return None

def _build_payload(content, embeds, components=None):
    payload: dict = {}
    if content is not None:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
    if components:
        payload["components"] = components
    return payload

def send_channel_message_with_attachments(
    channel_id: str,
    content: str | None = None,
    embeds: list | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
):
    """Send a message with file attachments."""
    _ensure_thread_postable(channel_id)
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    payload: dict[str, object] = {}
    if content is not None:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    # C4 Fix: Try REST with files FIRST (before gateway fallback)
    if attachments:
        files = {f"file{i}": (name, data, mime) for i, (name, data, mime) in enumerate(attachments)}
        files["payload_json"] = ("", json.dumps(payload), "application/json")
        try:
            r = _discord_request("POST", url, files=files)
            if r is not None and r.status_code < 400:
                return r.json()
            logger.warn("send w/ attachments REST failed", status=r.status_code if r else None, channel=channel_id)
        except Exception as e:
            logger.warn("send w/ attachments REST error", err=str(e)[:120])

    # No attachments, or REST failed — try plain REST
    try:
        r = _discord_request("POST", url, json_data=payload)
        if r is not None and r.status_code < 400:
            return r.json()
    except Exception as e:
        logger.warn("send REST failed, trying gateway", err=str(e)[:120])

    # Gateway fallback (no file attachments)
    try:
        from modules.manhwa.discord.gateway_sender import send_via_gateway
        if send_via_gateway(channel_id, content=content, embeds=embeds):
            return {"id": "gateway", "channel_id": channel_id}
    except Exception as e:
        logger.error("gateway fallback failed", err=str(e)[:160])
    return None
