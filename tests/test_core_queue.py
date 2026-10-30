from core import queue
from core.config import Settings


def test_redis_settings_parses_dsn(monkeypatch):
    monkeypatch.setattr(queue, "get_settings", lambda: Settings(redis_url="redis://127.0.0.1:6390/3"))
    rs = queue.redis_settings()
    assert rs.host == "127.0.0.1"
    assert rs.port == 6390
    assert rs.database == 3
