"""Discord delivery: webhook URLs and bot-channel messages.

Both transports share one retry policy (bounded, honours ``retry_after`` with
jitter) and one chunking rule (Discord rejects payloads with more than 10
embeds). The async paths used to skip chunking entirely and to mutate the
caller's embed dicts when emulating a webhook username — a latent 400 on any
batch over 10, and a surprising side effect for the caller.
"""

from __future__ import annotations

import asyncio
import os
import random

import httpx

from modules.media.common.log import get_logger

log = get_logger("discord")

_HEADERS = {"User-Agent": "DiscordBot (https://github.com/aldifhr/media-scrapers, 1.0)"}
_API = "https://discord.com/api/v10"
_MAX_RETRIES = 3
_MAX_EMBEDS = 10  # Discord rejects payloads with >10 embeds
_DEFAULT_TIMEOUT = 10.0


def _resolve_webhook(webhook: str = "", env_var: str = "DISCORD_WEBHOOK_URL") -> str:
    return (webhook or "").strip() or (os.environ.get(env_var) or "").strip()


def _validate_webhook(url: str) -> str:
    if not url.startswith("https://discord.com/api/webhooks/") and not url.startswith(
        "https://discordapp.com/api/webhooks/"
    ):
        raise ValueError("Invalid Discord webhook URL")
    return url


def _payload(embeds, content, username, avatar_url):
    payload: dict = {"embeds": list(embeds)}
    if content is not None:
        payload["content"] = content
    if username:
        payload["username"] = username
    if avatar_url:
        payload["avatar_url"] = avatar_url
    return payload


def _retry_after(response) -> float:
    try:
        return min(float(response.json().get("retry_after", 1)), 30.0)
    except (ValueError, TypeError, AttributeError):
        return 1.0


def _chunks(embeds, size: int = _MAX_EMBEDS):
    for start in range(0, len(embeds), size):
        yield embeds[start : start + size]


def _resolve_channel(token_env: str, channel_env: str) -> tuple[str, str]:
    return (
        (os.environ.get(token_env) or "").strip(),
        (os.environ.get(channel_env) or "").strip(),
    )


# --- webhook transport -----------------------------------------------------


def send_webhook(
    embeds,
    webhook="",
    env_var="DISCORD_WEBHOOK_URL",
    content=None,
    username=None,
    avatar_url=None,
    timeout=15,
) -> int:
    """Send embeds via a webhook URL. Returns the number of embeds accepted."""
    url = _resolve_webhook(webhook, env_var)
    if not url:
        log.info("no webhook configured (%s not set); skipping", env_var)
        return 0
    sent = 0
    try:
        for chunk in _chunks(embeds):
            _post_sync(
                _validate_webhook(url), _payload(chunk, content, username, avatar_url), timeout
            )
            sent += len(chunk)
    except Exception as exc:
        log.warning("webhook send failed: %s: %s", type(exc).__name__, exc)
    return sent


async def asend_webhook(
    embeds,
    webhook="",
    env_var="DISCORD_WEBHOOK_URL",
    content=None,
    username=None,
    avatar_url=None,
    timeout=_DEFAULT_TIMEOUT,
) -> int:
    url = _resolve_webhook(webhook, env_var)
    if not url:
        log.info("no webhook configured (%s not set); skipping", env_var)
        return 0
    sent = 0
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS) as client:
            for chunk in _chunks(embeds):
                await _post_async(
                    client, _validate_webhook(url), _payload(chunk, content, username, avatar_url)
                )
                sent += len(chunk)
    except Exception as exc:
        log.warning("async webhook send failed: %s: %s", type(exc).__name__, exc)
    return sent


def send_webhook_one(embed, **kwargs) -> bool:
    return send_webhook([embed], **kwargs) > 0


# --- bot-channel transport -------------------------------------------------
# Webhooks get deleted when the channel is recreated (404 forever). A bot
# token + channel id survives that, so a source can deliver via the bot API
# (POST /channels/{id}/messages) instead of a webhook URL.


def _bot_headers(token: str) -> dict:
    headers = dict(_HEADERS)
    headers["Authorization"] = f"Bot {token}"
    return headers


def _resolve_target(channel_id: str | None, token_env: str, channel_env: str) -> tuple[str, str]:
    """Resolve (token, target) where target is a channel OR a thread id.

    Discord models a thread as a channel, so ``POST /channels/{id}/messages``
    works unchanged for a thread id. ``channel_id`` overrides the env default,
    which is how per-post thread routing is done.
    """
    token, default = _resolve_channel(token_env, channel_env)
    return token, ((channel_id or "").strip() or default)


def send_channel(
    embeds,
    token_env="DISCORD_BOT_TOKEN",
    channel_env="DISCORD_CHANNEL_ID",
    content=None,
    username=None,
    avatar_url=None,
    timeout=15,
    channel_id: str | None = None,
) -> int:
    token, target = _resolve_target(channel_id, token_env, channel_env)
    if not token or not target:
        log.info("no bot channel configured (%s/%s not set); skipping", token_env, channel_env)
        return 0
    sent = 0
    try:
        for chunk in _chunks(embeds):
            payload = _payload(chunk, content, None, None)
            if username or avatar_url:
                # webhook-only fields; emulate with a footer on a copy so the
                # caller's embed is never mutated.
                payload["embeds"] = [
                    {**chunk[0], "footer": {"text": username or avatar_url}},
                    *chunk[1:],
                ]
            _post_sync(
                f"{_API}/channels/{target}/messages",
                payload,
                timeout,
                headers=_bot_headers(token),
            )
            sent += len(chunk)
    except Exception as exc:
        log.warning("channel send failed: %s: %s", type(exc).__name__, exc)
    return sent


async def asend_channel(
    embeds,
    token_env="DISCORD_BOT_TOKEN",
    channel_env="DISCORD_CHANNEL_ID",
    content=None,
    username=None,
    avatar_url=None,
    timeout=_DEFAULT_TIMEOUT,
    channel_id: str | None = None,
) -> int:
    token, target = _resolve_target(channel_id, token_env, channel_env)
    if not token or not target:
        log.info("no bot channel configured (%s/%s not set); skipping", token_env, channel_env)
        return 0
    sent = 0
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=_bot_headers(token)) as client:
            for chunk in _chunks(embeds):
                payload = _payload(chunk, content, None, None)
                if username or avatar_url:
                    payload["embeds"] = [
                        {**chunk[0], "footer": {"text": username or avatar_url}},
                        *chunk[1:],
                    ]
                await _post_async(client, f"{_API}/channels/{target}/messages", payload)
                sent += len(chunk)
    except Exception as exc:
        log.warning("async channel send failed: %s: %s", type(exc).__name__, exc)
    return sent


# --- shared retry policy ---------------------------------------------------


def _post_sync(url: str, payload: dict, timeout, headers=None) -> None:
    import requests

    for attempt in range(_MAX_RETRIES):
        response = requests.post(url, json=payload, headers=headers or _HEADERS, timeout=timeout)
        if response.status_code != 429:
            response.raise_for_status()
            return
        if attempt == _MAX_RETRIES - 1:
            response.raise_for_status()
        _sleep_sync(_retry_after(response))


def _sleep_sync(seconds: float) -> None:
    import time

    time.sleep(seconds + random.uniform(0, 0.25))


async def _post_async(client: httpx.AsyncClient, url: str, payload: dict) -> None:
    for attempt in range(_MAX_RETRIES):
        response = await client.post(url, json=payload)
        if response.status_code != 429:
            response.raise_for_status()
            return
        if attempt == _MAX_RETRIES - 1:
            response.raise_for_status()
        await asyncio.sleep(_retry_after(response) + random.uniform(0, 0.25))


if __name__ == "__main__":
    assert _payload([], "x", None, None)["content"] == "x"
    assert list(_chunks(list(range(25)), 10)) == [
        list(range(10)),
        list(range(10, 20)),
        list(range(20, 25)),
    ]
    print("common.discord self-check passed")
