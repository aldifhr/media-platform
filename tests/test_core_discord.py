from core import discord
from core.config import Settings


def test_verify_false_without_public_key(monkeypatch):
    monkeypatch.setattr(discord, "get_settings", lambda: Settings(discord_public_key=""))
    assert discord.verify_interaction(b"body", "sig", "ts") is False


def test_verify_false_on_bad_signature(monkeypatch):
    # a syntactically valid but wrong key must not raise, just return False
    monkeypatch.setattr(
        discord,
        "get_settings",
        lambda: Settings(discord_public_key="0" * 64),
    )
    assert discord.verify_interaction(b"body", "badsig", "0") is False
