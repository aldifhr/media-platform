import asyncio

from apps.discord_bot import jobs


class FakeSession:
    def __init__(self):
        self.executed = []
        self.committed = False

    async def execute(self, clause, params=None):
        self.executed.append(params)
        return self

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch(monkeypatch, send_result):
    sess = FakeSession()

    async def fake_send(channel, content="", embeds=None):
        return send_result

    monkeypatch.setattr(jobs, "send_channel_message", fake_send)
    monkeypatch.setattr(jobs, "get_sessionmaker", lambda: lambda: sess)
    return sess


def test_deliver_sent_records_history(monkeypatch):
    sess = _patch(monkeypatch, {"id": "m1"})
    status = asyncio.run(
        jobs.deliver_message(
            {}, event_id="e1", channel_id=100, content="hi", thread_id=555, guild_id=1
        )
    )
    assert status == "sent"
    assert sess.committed
    assert sess.executed[0]["channel_id"] == 555  # thread is the target
    assert sess.executed[0]["status"] == "sent"


def test_deliver_failed_records_history(monkeypatch):
    sess = _patch(monkeypatch, None)
    status = asyncio.run(jobs.deliver_message({}, event_id="e1", channel_id=100))
    assert status == "failed"
    assert sess.executed[0]["status"] == "failed"
    assert sess.executed[0]["channel_id"] == 100


def test_discord_worker_settings_has_deliver_job():
    assert jobs.deliver_message in jobs.DiscordWorkerSettings.functions
