import asyncio

from core.notifications import engine


class FakeRepo:
    def __init__(self, rows):
        self.rows = rows
        self.delivered = []
        self.failed = []

    async def claim_batch(self, session, limit=50):
        return self.rows

    async def mark_delivered(self, session, row_id):
        self.delivered.append(row_id)

    async def mark_failed(self, session, row_id, err, attempts, max_attempts):
        self.failed.append((row_id, attempts))


class FakeSession:
    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True


ROWS = [
    {
        "id": 1,
        "event_id": "e1",
        "event_type": "manhwa.chapter.released",
        "version": 1,
        "source": "ikiru",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "payload": {"category": "ikiru", "title": "Solo Leveling"},
        "attempts": 0,
        "max_attempts": 8,
    }
]
SUBS = [{"guild_id": 10, "channel_id": 100, "module": "manhwa", "category": None, "enabled": True}]
ROUTES = [
    {
        "event_type": "manhwa.chapter.released",
        "rule": {"category": "ikiru"},
        "thread_id": 555,
        "priority": 10,
        "enabled": True,
    }
]


def test_plan_deliveries():
    intents = engine.plan_deliveries(ROWS, SUBS, ROUTES)
    assert len(intents) == 1
    i = intents[0]
    assert (i.guild_id, i.channel_id, i.thread_id) == (10, 100, 555)
    assert i.event_id == "e1"


def test_process_batch_enqueues_and_marks_delivered(monkeypatch):
    enq = []

    async def enqueue(name, *a, **kw):
        enq.append(kw)
        return object()

    monkeypatch.setattr(engine.queue, "enqueue", enqueue)
    repo = FakeRepo(ROWS)
    eng = engine.NotificationEngine(repo=repo)
    s = FakeSession()
    n = asyncio.run(eng.process_batch(s, SUBS, ROUTES))
    assert n == 1
    assert len(enq) == 1 and enq[0]["channel_id"] == 100 and enq[0]["thread_id"] == 555
    assert repo.delivered == [1]
    assert repo.failed == []
    assert s.committed


def test_process_batch_enqueue_failure_marks_failed(monkeypatch):
    async def boom(name, *a, **kw):
        raise RuntimeError("redis down")

    monkeypatch.setattr(engine.queue, "enqueue", boom)
    repo = FakeRepo(ROWS)
    eng = engine.NotificationEngine(repo=repo)
    s = FakeSession()
    n = asyncio.run(eng.process_batch(s, SUBS, ROUTES))
    assert n == 0
    assert repo.failed and repo.failed[0][0] == 1
    assert repo.delivered == []


def test_process_batch_empty():
    repo = FakeRepo([])
    eng = engine.NotificationEngine(repo=repo)
    assert asyncio.run(eng.process_batch(FakeSession(), SUBS, ROUTES)) == 0
