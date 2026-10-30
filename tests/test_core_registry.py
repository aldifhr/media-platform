from core.plugins import Source, SourceMeta, SourceRegistry


class FakeSource(Source):
    def __init__(self, sid: str, category: str = "media", enabled: bool = True):
        self.meta = SourceMeta(id=sid, category=category, enabled=enabled)

    async def fetch(self):
        return []

    def normalize(self, raw):
        return raw


def test_register_and_get():
    r = SourceRegistry()
    s = FakeSource("pahe")
    r.register(s)
    assert r.get("pahe") is s
    assert s in r.all()


def test_enabled_filter_by_category_and_flag():
    r = SourceRegistry()
    r.register(FakeSource("pahe", "media", True))
    r.register(FakeSource("ikiru", "manhwa", True))
    r.register(FakeSource("dead", "media", False))
    assert {x.meta.id for x in r.enabled()} == {"pahe", "ikiru"}
    assert {x.meta.id for x in r.enabled("media")} == {"pahe"}
    assert {x.meta.id for x in r.enabled("manhwa")} == {"ikiru"}
