from fastapi.testclient import TestClient

from apps.discord_bot import interactions

HEADERS = {"X-Signature-Ed25519": "sig", "X-Signature-Timestamp": "1"}


def _client(monkeypatch, ok: bool = True) -> TestClient:
    monkeypatch.setattr(interactions, "verify_interaction", lambda b, s, t: ok)
    return TestClient(interactions.create_app())


def test_ping_returns_pong(monkeypatch):
    r = _client(monkeypatch).post("/interactions", json={"type": 1}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"type": 1}


def test_bad_signature_401(monkeypatch):
    r = _client(monkeypatch, ok=False).post("/interactions", json={"type": 1}, headers=HEADERS)
    assert r.status_code == 401


def test_application_command_defers(monkeypatch):
    r = _client(monkeypatch).post(
        "/interactions", json={"type": 2, "data": {"name": "manhwa"}}, headers=HEADERS
    )
    assert r.status_code == 200
    assert r.json()["type"] == 5


def test_message_component_defers(monkeypatch):
    r = _client(monkeypatch).post("/interactions", json={"type": 3}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["type"] == 5


def test_unknown_type_400(monkeypatch):
    r = _client(monkeypatch).post("/interactions", json={"type": 99}, headers=HEADERS)
    assert r.status_code == 400


def test_health(monkeypatch):
    assert _client(monkeypatch).get("/health").json() == {"ok": True}
