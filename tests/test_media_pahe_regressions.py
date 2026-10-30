"""Regression tests for media-scrapers.

Each test names the bug it locks down. Run with ``.venv/bin/python -m pytest -q``.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


class _LogCapture(logging.Handler):
    """Collects records from the ``media`` logger.

    ``common.log`` sets ``propagate = False`` so a record is emitted exactly
    once, which also means pytest's ``caplog`` (which listens on the root
    logger) never sees it. Attaching directly is the honest way round.
    """

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def capture_logs(level: int = logging.WARNING):
    logger = logging.getLogger("media")
    handler = _LogCapture()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PAHE_API_TOKEN", "test-token")
    monkeypatch.setenv("MEDIA_API_TOKEN", "test-token")
    monkeypatch.setenv("PAHE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PAHE_DISCORD_TOKEN", "bot-token")
    monkeypatch.setenv("PAHE_DISCORD_CHANNEL", "12345")
    monkeypatch.setenv("PAHE_MIN_YEAR", "0")
    yield


def _module(name: str):
    top = name.split(".")[0]
    if top == "pahe":
        name = "modules.media.sources." + name
    elif top in ("common", "scheduler", "source_health"):
        name = "modules.media." + name
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# common.env — one loader, real environment always wins
# ---------------------------------------------------------------------------


def test_env_loader_does_not_clobber_real_environment(monkeypatch, tmp_path):
    """pahe/main.py and pahe/scanner/main.py used to assign .env values
    unconditionally, so a value in .env overrode an explicitly exported
    variable — including the token a test sets."""
    from modules.media.common import env as env_mod

    dotenv = tmp_path / ".env"
    dotenv.write_text("SOME_SETTING=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("SOME_SETTING", "from-real-env")

    env_mod._LOADED = False
    applied = env_mod.load_env(dotenv)

    assert applied == {}
    assert env_mod.env_str("SOME_SETTING") == "from-real-env"
    env_mod._LOADED = False


def test_env_helpers_tolerate_garbage():
    from modules.media.common import env as env_mod

    assert env_mod.env_int("DEFINITELY_UNSET_INT", 7) == 7
    with patch.dict("os.environ", {"DEFINITELY_UNSET_INT": "not-a-number"}):
        assert env_mod.env_int("DEFINITELY_UNSET_INT", 7) == 7
    with patch.dict("os.environ", {"DEFINITELY_UNSET_LIST": " a , b ,, c "}):
        assert env_mod.env_list("DEFINITELY_UNSET_LIST") == ["a", "b", "c"]
    with patch.dict("os.environ", {"DEFINITELY_UNSET_TAGS": "ok,../etc/passwd,also-ok"}):
        assert env_mod.env_list("DEFINITELY_UNSET_TAGS", pattern=r"[A-Za-z0-9_-]+") == [
            "ok",
            "also-ok",
        ]


# ---------------------------------------------------------------------------
# common.auth — one token guard, fail closed
# ---------------------------------------------------------------------------


def test_auth_accepts_bearer_or_query_and_fails_closed():
    from modules.media.common import auth

    with patch.dict("os.environ", {"PAHE_API_TOKEN": "sekret"}):
        assert auth.check({"authorization": "Bearer sekret"}, {}, "PAHE_API_TOKEN")
        assert auth.check({}, {"token": "sekret"}, "PAHE_API_TOKEN")
        assert not auth.check({"authorization": "Bearer wrong"}, {}, "PAHE_API_TOKEN")
        assert not auth.check({}, {"token": "wrong"}, "PAHE_API_TOKEN")
        assert not auth.check({}, {}, "PAHE_API_TOKEN")

    # No token configured -> deny, never allow.
    with patch.dict("os.environ", {"PAHE_API_TOKEN": ""}):
        assert not auth.check({"authorization": "Bearer "}, {}, "PAHE_API_TOKEN")

    # A stub missing .get() must deny rather than raise.
    assert not auth.check(object(), object(), "PAHE_API_TOKEN")


# ---------------------------------------------------------------------------
# common.state — honours PAHE_STATE_DIR, reentrant, atomic
# ---------------------------------------------------------------------------


def test_state_uses_pa_state_dir(tmp_path):
    from modules.media.common.state import State

    state = State("pahe")
    assert Path(state.path).parent == tmp_path
    state.mark_seen("a", value={"title": "A"})
    assert state.is_seen("a")
    assert state.count() == 1
    assert not (tmp_path / "pahe_seen.json.tmp").exists()


def test_state_prune_drops_old_entries(tmp_path):
    from modules.media.common.state import State

    state = State("pahe")
    state.mark_seen("old", value={"seenAt": "2020-01-01T00:00:00+00:00"})
    state.mark_seen("new", value={"seenAt": "2100-01-01T00:00:00+00:00"})
    assert state.prune(days=2) == 1
    assert not state.is_seen("old")
    assert state.is_seen("new")


# ---------------------------------------------------------------------------
# source_health — respects PAHE_STATE_DIR, quarantines on 3 strikes
# ---------------------------------------------------------------------------


def test_source_health_quarantines_after_three_failures(tmp_path):
    import modules.media.source_health as sh

    assert sh.should_skip("pahe") == (False, None)
    for _ in range(3):
        sh.record("pahe", ok=False, error="boom")
    skip, reason = sh.should_skip("pahe")
    assert skip
    assert "quarantined" in reason
    assert (tmp_path / "source_health.json").exists()

    # A success resets the strike count but does NOT lift an active
    # quarantine — recovery is time-based, by design.
    sh.record("pahe", ok=True)
    assert sh.snapshot()["pahe"]["consecutive_failures"] == 0
    assert sh.should_skip("pahe")[0] is True


# ---------------------------------------------------------------------------
# pahe.scanner.rules — the freshness rule, one definition
# ---------------------------------------------------------------------------


def test_new_post_rule():
    """Regression: the notifier once hardcoded `year >= 2025`, which muted
    pahe entirely — pahe back-fills old films, so release year is not a
    freshness signal, and serials have no year at all."""
    from modules.media.sources.pahe.scanner.rules import is_new_post

    @dataclass
    class Post:
        id: str
        year: int | None
        title: str = ""

    class Seen:
        def __init__(self, keys=()):
            self.keys = set(keys)

        def is_seen(self, key):
            return key in self.keys

    empty = Seen()
    # Serials (year=None) always notify — "[Episode N Added]" is fresh.
    assert is_new_post(Post("s1", None), empty, 2026)
    # Movies respect PAHE_MIN_YEAR.
    assert is_new_post(Post("m1", 2026), empty, 2026)
    assert not is_new_post(Post("m2", 1989), empty, 2026)
    assert not is_new_post(Post("m3", 2025), empty, 2026)
    # min_year off -> everything new gets through.
    assert is_new_post(Post("m4", 1989), empty, 0)
    # Already-seen posts never re-notify, whatever the year.
    assert not is_new_post(Post("m1", 2026), Seen({"m1"}), 2026)
    assert not is_new_post(Post("s1", None), Seen({"s1"}), 2026)


def test_season_titled_post_with_a_year_still_bypasses_the_gate():
    """pahe sometimes emits a release year for a series. A year-gated rule
    would then drop a brand-new episode of an older show — the season
    blacklist the rule is supposed to prevent."""
    from modules.media.sources.pahe.scanner.rules import is_new_post, is_serial

    @dataclass
    class Post:
        id: str
        year: int | None
        title: str = ""

    class Seen:
        def is_seen(self, key):
            return False

    seen = Seen()
    for title in (
        "Dark Matter (2024) Season 2 Complete WEB-DL",
        "MobLand (2025) Season 2 WEB-DL [Episode 7 Added]",
        "Some Show S02E07 WEB-DL 1080p",
        "Older Drama (2019) Season 1 Episode 3 Added",
    ):
        post = Post("x", 2019, title)
        assert is_serial(post), title
        assert is_new_post(post, seen, 2026), title

    # A film with a year in the title is still a film.
    film = Post("f", 2019, "Shaun of the Dead (2004) WEB-DL")
    assert not is_serial(film)
    assert not is_new_post(film, seen, 2026)


def test_recheck_includes_serials():
    """A new episode changes a serial's download block, which is exactly what
    recheck exists to catch. Serials used to be excluded by the year gate."""
    from modules.media.sources.pahe.scanner.rules import is_recheck_candidate

    @dataclass
    class Post:
        id: str
        year: int | None
        title: str = ""

    class Seen:
        def __init__(self, keys=()):
            self.keys = set(keys)

        def is_seen(self, key):
            return key in self.keys

    seen = Seen({"s1", "m-old", "m-new"})
    # Serial, no year -> candidate.
    assert is_recheck_candidate(Post("s1", None, "MobLand S2 E7"), seen, 2026)
    # Serial WITH a year -> still a candidate.
    assert is_recheck_candidate(Post("s1", 2024, "Dark Matter (2024) S2"), seen, 2026)
    # Old movie -> not a candidate.
    assert not is_recheck_candidate(Post("m-old", 1989, "Dead Poets Society"), seen, 2026)
    # New movie -> candidate.
    assert is_recheck_candidate(Post("m-new", 2026, "Ghost in the Cell"), seen, 2026)
    # Unseen post -> not a candidate.
    assert not is_recheck_candidate(Post("nope", 2026, "Ghost in the Cell"), seen, 2026)


# ---------------------------------------------------------------------------
# pahe.scanner.scraper — never raise on one bad post; skip seen detail fetches
# ---------------------------------------------------------------------------


def _post_html(with_download_box: bool = True) -> str:
    box = (
        '<div class="box download"><b>720p x264</b>'
        '<a class="shortc-button" href="https://x.test/dl">Download</a></div>'
        if with_download_box
        else ""
    )
    return f"<html><body><article><img src='/poster.jpg'><p>Synopsis here.</p>{box}</article></body></html>"


def test_scrape_post_details_returns_all_keys_without_download_box():
    """Regression: `filtered` was only assigned inside the download-box loop,
    so a post with no box raised UnboundLocalError and 500'd the whole check."""
    from modules.media.sources.pahe.scanner import scraper

    async def fake_get(url):
        return _post_html(with_download_box=False)

    with patch.object(scraper, "_get", fake_get):
        details = asyncio.run(scraper.scrape_post_details("https://pahe.ink/post"))

    assert set(details) == {"image", "synopsis", "downloads"}
    assert details["downloads"] == []
    assert details["image"] == "https://pahe.ink/poster.jpg"


def test_scrape_post_details_keeps_notified_qualities():
    from modules.media.sources.pahe.scanner import scraper

    html = (
        "<html><body><article>"
        '<div class="box download"><b>480p x264</b>'
        '<a class="shortc-button" href="https://x.test/480">480</a></div>'
        '<div class="box download"><b>1080p x264</b>'
        '<a class="shortc-button" href="https://x.test/1080">1080</a></div>'
        "</article></body></html>"
    )

    async def fake_get(url):
        return html

    with patch.object(scraper, "_get", fake_get):
        details = asyncio.run(scraper.scrape_post_details("https://pahe.ink/post"))

    assert [d["url"] for d in details["downloads"]] == ["https://x.test/1080"]


def test_scrape_post_details_rejects_non_https_image():
    from modules.media.sources.pahe.scanner import scraper

    html = "<html><body><article><img src='data:image/png;base64,AAA'></article></body></html>"

    async def fake_get(url):
        return html

    with patch.object(scraper, "_get", fake_get):
        details = asyncio.run(scraper.scrape_post_details("https://pahe.ink/post"))

    assert details["image"] is None


def test_scrape_latest_posts_skips_details_for_seen_posts():
    """A check pulls ~16 posts and used to fetch all 16 pages even when
    nothing was new — 16 HTTP round-trips every 30 minutes to re-learn URLs
    already stored."""
    from modules.media.sources.pahe.scanner import scraper

    @dataclass
    class Post:
        id: str
        slug: str = ""
        title: str = ""
        url: str = ""
        description: str = ""
        pub_date: str = ""
        year: int | None = None
        image: str | None = None
        synopsis: str = ""
        downloads: list = field(default_factory=list)
        genres: list = field(default_factory=list)
        qualities: list = field(default_factory=list)

    posts = [Post(id=str(i), url=f"https://pahe.ink/{i}") for i in range(4)]
    fetched: list[str] = []

    async def fake_feed(url):
        return list(posts)

    async def fake_details(url):
        fetched.append(url)
        return {"image": None, "synopsis": "", "downloads": []}

    with (
        patch.object(scraper, "_safe_feed", fake_feed),
        patch.object(scraper, "scrape_post_details", fake_details),
    ):
        result = asyncio.run(
            scraper.scrape_latest_posts([], needs_details=lambda p: p.id in {"1", "3"})
        )

    assert sorted(fetched) == ["https://pahe.ink/1", "https://pahe.ink/3"]
    assert [p.id for p in result] == ["0", "1", "2", "3"]


def test_scrape_latest_posts_dedupes_a_post_present_in_two_feeds():
    """A post tagged into two feeds appears in both. The results used to be
    concatenated as-is, so the same title was notified twice and its page
    fetched twice."""
    from modules.media.sources.pahe.scanner import scraper

    @dataclass
    class Post:
        id: str
        slug: str = ""
        title: str = ""
        url: str = ""
        description: str = ""
        pub_date: str = ""
        year: int | None = None
        image: str | None = None
        synopsis: str = ""
        downloads: list = field(default_factory=list)
        genres: list = field(default_factory=list)
        qualities: list = field(default_factory=list)

    shared = Post(id="42", url="https://pahe.ink/42", title="Dup")
    other = Post(id="7", url="https://pahe.ink/7", title="Other")

    async def fake_feed(url):
        # both tag feeds carry the shared post; only one carries "other"
        return [shared, other] if "2026" in url else [shared]

    fetched: list[str] = []

    async def fake_details(url):
        fetched.append(url)
        return {"image": None, "synopsis": "", "downloads": []}

    with (
        patch.object(scraper, "_safe_feed", fake_feed),
        patch.object(scraper, "scrape_post_details", fake_details),
    ):
        posts = asyncio.run(scraper.scrape_latest_posts(["2026", "k-drama"]))

    assert [p.id for p in posts] == ["42", "7"]
    assert fetched.count("https://pahe.ink/42") == 1


def test_scrape_latest_posts_raises_when_every_feed_fails():
    """A dead or reformatted feed used to be swallowed and reported as
    ``{"ok": true, "checked": 0}`` — health stayed green and notifications
    stopped with no signal at all."""
    from modules.media.sources.pahe.scanner import scraper

    async def dead_feed(url):
        raise ConnectionError("refused")

    # Patch scrape_feed, not _safe_feed: _safe_feed's own try/except is what
    # turns the raise into a None result.
    with patch.object(scraper, "scrape_feed", dead_feed), pytest.raises(scraper.FeedUnavailable):
        asyncio.run(scraper.scrape_latest_posts([]))


def test_scrape_latest_posts_tolerates_one_dead_feed_of_two():
    from modules.media.sources.pahe.scanner import scraper

    @dataclass
    class Post:
        id: str
        slug: str = ""
        title: str = ""
        url: str = ""
        description: str = ""
        pub_date: str = ""
        year: int | None = None
        image: str | None = None
        synopsis: str = ""
        downloads: list = field(default_factory=list)
        genres: list = field(default_factory=list)
        qualities: list = field(default_factory=list)

    async def fake_feed(url):
        if "2026" in url:
            raise ConnectionError("refused")
        return [Post(id="1", url="https://pahe.ink/1")]

    with patch.object(scraper, "scrape_feed", fake_feed):
        posts = asyncio.run(scraper.scrape_latest_posts(["2026", "k-drama"]))

    assert [p.id for p in posts] == ["1"]


def test_scrape_feed_reads_atom_as_well_as_rss():
    """``root.iter("item")`` silently returns nothing for an Atom feed, so an
    upstream format switch would look exactly like "no new posts"."""
    from modules.media.sources.pahe.scanner import scraper

    atom = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>MobLand Season 2</title>
        <link href="https://pahe.ink/mobland-s2/"/>
        <id>tag:pahe.ink,2026:mobland-s2</id>
        <published>Sun, 12 Jul 2026 10:00:44 +0000</published>
        <category term="k-drama"/>
        <category term="1080p"/>
      </entry>
    </feed>"""

    async def fake_get(url):
        return atom

    with patch.object(scraper, "_get", fake_get):
        posts = asyncio.run(scraper.scrape_feed("https://pahe.ink/feed/"))

    assert len(posts) == 1
    assert posts[0].title == "MobLand Season 2"
    assert posts[0].url == "https://pahe.ink/mobland-s2/"
    # No ?p= in the Atom id, so it falls back to the URL — stable and distinct.
    assert posts[0].id == "https://pahe.ink/mobland-s2"
    assert posts[0].genres == ["k-drama"]
    assert posts[0].qualities == ["1080p"]


def test_scrape_feed_well_formed_html_yields_no_posts_and_is_flagged():
    """An HTML error page is often well-formed XML, so it parses cleanly and
    returns zero entries. Zero entries from pahe's realtime feed means "we
    could not read this", so the caller must treat it as unusable rather than
    as "nothing new"."""
    from modules.media.sources.pahe.scanner import scraper

    async def fake_get(url):
        return "<html><body>502 Bad Gateway</body></html>"

    with patch.object(scraper, "_get", fake_get):
        posts = asyncio.run(scraper.scrape_feed("https://pahe.ink/feed/"))

    assert posts == []

    async def dead_get(url):
        return "<html><body>502 Bad Gateway</body></html>"

    with patch.object(scraper, "_get", dead_get), pytest.raises(scraper.FeedUnavailable):
        asyncio.run(scraper.scrape_latest_posts([]))


def test_scrape_latest_posts_survives_one_broken_post():
    from modules.media.sources.pahe.scanner import scraper

    @dataclass
    class Post:
        id: str
        slug: str = ""
        title: str = ""
        url: str = ""
        description: str = ""
        pub_date: str = ""
        year: int | None = None
        image: str | None = None
        synopsis: str = ""
        downloads: list = field(default_factory=list)
        genres: list = field(default_factory=list)
        qualities: list = field(default_factory=list)

    posts = [Post(id="a", url="https://pahe.ink/a"), Post(id="b", url="https://pahe.ink/b")]

    async def fake_feed(url):
        return list(posts)

    async def fake_details(url):
        if url.endswith("/a"):
            raise RuntimeError("kaboom")
        return {"image": None, "synopsis": "ok", "downloads": []}

    with (
        patch.object(scraper, "_safe_feed", fake_feed),
        patch.object(scraper, "scrape_post_details", fake_details),
    ):
        result = asyncio.run(scraper.scrape_latest_posts([]))

    assert [p.id for p in result] == ["a", "b"]
    assert result[1].synopsis == "ok"


def test_http_client_is_rebound_when_the_event_loop_changes():
    """httpx binds a client to the loop that created it and raises
    "Event loop is closed" the moment another loop touches it. The client is
    now tracked per loop and discarded, so a second loop gets a fresh one."""
    from modules.media.sources.pahe.scanner import scraper

    async def grab():
        return scraper._http()

    first = asyncio.run(grab())
    second = asyncio.run(grab())
    assert first is not second
    asyncio.run(scraper.aclose_http())


# ---------------------------------------------------------------------------
# common.discord — chunking, no caller mutation
# ---------------------------------------------------------------------------


def test_discord_chunks_embeds_and_does_not_mutate_caller():
    from modules.media.common import discord

    embeds = [{"title": str(i)} for i in range(25)]
    original = json.dumps(embeds)

    payloads: list[dict] = []

    def fake_post(url, payload, timeout, headers=None):
        payloads.append(payload)

    with (
        patch.dict("os.environ", {"T": "tok", "C": "chan"}),
        patch.object(discord, "_post_sync", fake_post),
    ):
        sent = discord.send_channel(embeds, token_env="T", channel_env="C", username="Pahe")

    assert sent == 25
    assert [len(p["embeds"]) for p in payloads] == [10, 10, 5]
    # The caller's embeds are untouched.
    assert json.dumps(embeds) == original
    assert "footer" not in embeds[0]


def test_discord_async_paths_chunk_too():
    from modules.media.common import discord

    embeds = [{"title": str(i)} for i in range(12)]
    seen_lengths: list[int] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            seen_lengths.append(len(json["embeds"]))
            return FakeResponse()

    with (
        patch.dict("os.environ", {"T": "tok", "C": "chan"}),
        patch.object(discord.httpx, "AsyncClient", lambda **kw: FakeClient()),
    ):
        sent = asyncio.run(discord.asend_channel(embeds, token_env="T", channel_env="C"))

    assert sent == 12
    assert seen_lengths == [10, 2]


# ---------------------------------------------------------------------------
# pahe.scanner.notify — Discord limits, bulk resilience
# ---------------------------------------------------------------------------


def test_notify_clamps_embed_fields():
    from modules.media.sources.pahe.scanner import notify

    @dataclass
    class Post:
        id: str
        title: str
        url: str
        description: str = ""
        pub_date: str = ""
        genres: list = field(default_factory=list)
        qualities: list = field(default_factory=list)
        year: int | None = None
        image: str | None = None
        synopsis: str = ""
        downloads: list = field(default_factory=list)

    post = Post(id="1", title="T" * 500, url="https://pahe.ink/1", genres=["g"] * 400)

    captured: list[dict] = []

    async def fake_send(embeds, **kwargs):
        captured.append(embeds[0])
        return 1

    with (
        patch.object(notify, "asend_channel", fake_send),
        patch.object(notify, "atranslate_to_id", AsyncMock(side_effect=lambda t: t)),
    ):
        ok = asyncio.run(notify.send_notification(post))

    assert ok
    embed = captured[0]
    assert len(embed["title"]) <= 256
    assert all(len(f["value"]) <= 1024 for f in embed["fields"])


def test_bulk_notifications_survive_a_raising_post():
    from modules.media.sources.pahe.scanner import notify

    @dataclass
    class Post:
        id: str

    async def fake_send(post):
        if post.id == "b":
            raise RuntimeError("discord down")
        return True

    with patch.object(notify, "send_notification", fake_send):
        result = asyncio.run(notify.send_bulk_notifications([Post("a"), Post("b"), Post("c")]))

    assert result["success"] == 2
    assert result["failed"] == 1
    assert result["results"] == [
        {"id": "a", "ok": True},
        {"id": "b", "ok": False},
        {"id": "c", "ok": True},
    ]


def test_bulk_notifications_empty_batch():
    from modules.media.sources.pahe.scanner import notify

    assert asyncio.run(notify.send_bulk_notifications([])) == {
        "success": 0,
        "failed": 0,
        "results": [],
    }


# ---------------------------------------------------------------------------
# Thread routing
# ---------------------------------------------------------------------------


def _routable_post(year=None, genres=(), title="Some Title"):
    from modules.media.sources.pahe.scanner.scraper import Post

    return Post(
        id="1",
        slug="x",
        title=title,
        url="https://pahe.ink/x",
        description="",
        pub_date="",
        genres=list(genres),
        year=year,
    )


def test_thread_routing_by_release_year():
    from modules.media.sources.pahe.scanner import notify

    env = {"PAHE_THREAD_2026": "111", "PAHE_THREAD_2025": "222"}
    with patch.dict("os.environ", env):
        # 2026 and later -> 2026 thread, whatever the genre.
        assert notify.target_channel(_routable_post(2026, ["Horror"])) == "111"
        assert notify.target_channel(_routable_post(2026, ["Comedy"])) == "111"
        assert notify.target_channel(_routable_post(2027, ["Drama"])) == "111"
        # Exactly 2025 -> 2025 thread.
        assert notify.target_channel(_routable_post(2025, ["Comedy"])) == "222"
        assert notify.target_channel(_routable_post(2025, ["Horror"])) == "222"
        # Older film -> default channel.
        assert notify.target_channel(_routable_post(2007, ["Horror"])) == ""
        assert notify.target_channel(_routable_post(1991, ["Horror"])) == ""
        # Serial (no year) -> default channel.
        assert notify.target_channel(_routable_post(None, ["Horror"])) == ""
        assert notify.target_channel(_routable_post(None, ["Comedy"])) == ""


def test_thread_routing_unset_thread_falls_back_to_default():
    from modules.media.sources.pahe.scanner import notify

    with patch.dict("os.environ", {"PAHE_THREAD_2026": "", "PAHE_THREAD_2025": ""}):
        assert notify.target_channel(_routable_post(2026, ["Horror"])) == ""


def test_send_notification_posts_to_the_resolved_thread():
    from modules.media.sources.pahe.scanner import notify

    post = _routable_post(2026, ["Comedy"], "Ghost in the Cell (2026)")
    captured: dict = {}

    async def fake_send(embeds, **kwargs):
        captured.update(kwargs)
        return 1

    with (
        patch.dict("os.environ", {"PAHE_THREAD_2026": "111", "PAHE_THREAD_2025": "222"}),
        patch.object(notify, "asend_channel", fake_send),
        patch.object(notify, "atranslate_to_id", AsyncMock(side_effect=lambda t: t)),
    ):
        ok = asyncio.run(notify.send_notification(post))

    assert ok
    assert captured["channel_id"] == "111"
    assert captured["token_env"] == "PAHE_DISCORD_TOKEN"


def test_send_notification_2025_post_goes_to_the_2025_thread():
    from modules.media.sources.pahe.scanner import notify

    post = _routable_post(2025, ["Drama"], "Some 2025 Film")
    captured: dict = {}

    async def fake_send(embeds, **kwargs):
        captured.update(kwargs)
        return 1

    with (
        patch.dict("os.environ", {"PAHE_THREAD_2026": "111", "PAHE_THREAD_2025": "222"}),
        patch.object(notify, "asend_channel", fake_send),
        patch.object(notify, "atranslate_to_id", AsyncMock(side_effect=lambda t: t)),
    ):
        asyncio.run(notify.send_notification(post))

    assert captured["channel_id"] == "222"


def test_threads_only_mode_drops_unrouted_posts_loudly():
    """With PAHE_DISCORD_CHANNEL empty, a post matching no rule has nowhere to
    go. It must not be sent, and it must not be dropped silently — a quiet
    drop is how a notifier dies without anyone noticing."""
    from modules.media.sources.pahe.scanner import notify

    post = _routable_post(2016, ["Comedy", "Drama"], "Bridget Jones's Baby (2016)")

    async def fake_send(embeds, **kwargs):
        raise AssertionError("must not send when there is no destination")

    with (
        patch.dict(
            "os.environ",
            {"PAHE_THREAD_2026": "111", "PAHE_THREAD_2025": "222", "PAHE_DISCORD_CHANNEL": ""},
        ),
        patch.object(notify, "asend_channel", fake_send),
        patch.object(notify, "atranslate_to_id", AsyncMock(side_effect=lambda t: t)),
        capture_logs() as logs,
    ):
        sent = asyncio.run(notify.send_notification(post))

    assert sent is False
    assert any("DROPPED" in m for m in logs.messages)
    assert any("Bridget Jones" in m for m in logs.messages)


def test_default_channel_still_used_when_configured():
    from modules.media.sources.pahe.scanner import notify

    post = _routable_post(2016, ["Comedy"], "Bridget Jones's Baby (2016)")
    captured: dict = {}

    async def fake_send(embeds, **kwargs):
        captured.update(kwargs)
        return 1

    with (
        patch.dict(
            "os.environ",
            {
                "PAHE_THREAD_2026": "111",
                "PAHE_THREAD_2025": "222",
                "PAHE_DISCORD_CHANNEL": "999",
            },
        ),
        patch.object(notify, "asend_channel", fake_send),
        patch.object(notify, "atranslate_to_id", AsyncMock(side_effect=lambda t: t)),
    ):
        assert asyncio.run(notify.send_notification(post))

    # Empty channel_id means "use the env default", which is the flat channel.
    assert captured["channel_id"] == ""
    assert captured["channel_env"] == "PAHE_DISCORD_CHANNEL"


# ---------------------------------------------------------------------------
# pahe.scanner.service — the shared check
# ---------------------------------------------------------------------------


def _service_post(post_id: str, year: int | None = None, title: str = "T"):
    from modules.media.sources.pahe.scanner.scraper import Post

    return Post(
        id=post_id,
        slug=post_id,
        title=title,
        url=f"https://pahe.ink/{post_id}",
        description="",
        pub_date="",
        year=year,
    )


def test_run_check_notifies_new_posts_and_marks_them_seen(tmp_path):
    from modules.media.sources.pahe.scanner import service

    posts = [_service_post("1", 2026, "Movie One"), _service_post("2", None, "Serial Two")]

    async def fake_scrape(tags, *, needs_details=None, concurrency=8):
        return posts

    sent: list[str] = []

    async def fake_bulk(batch):
        sent.extend(p.id for p in batch)
        return {
            "success": len(batch),
            "failed": 0,
            "results": [{"id": p.id, "ok": True} for p in batch],
        }

    with (
        patch.object(service.scraper, "scrape_latest_posts", fake_scrape),
        patch.object(service, "send_bulk_notifications", fake_bulk),
    ):
        result = asyncio.run(service.run_check())

    body = json.loads(result["body"])
    assert result["status"] == 200
    assert body["new"] == 2
    assert sorted(sent) == ["1", "2"]
    assert service.SEEN.is_seen("1")
    assert service.SEEN.is_seen("2")


def test_run_check_dry_run_does_not_touch_seen_state(tmp_path):
    """Regression: the old dry-run path marked every post seen, so a single
    dry run muted the source until the state was pruned."""
    from modules.media.sources.pahe.scanner import service

    posts = [_service_post("1", 2026, "Movie One")]

    async def fake_scrape(tags, *, needs_details=None, concurrency=8):
        return posts

    async def fake_bulk(batch):
        raise AssertionError("dry run must not notify")

    with (
        patch.object(service.scraper, "scrape_latest_posts", fake_scrape),
        patch.object(service, "send_bulk_notifications", fake_bulk),
    ):
        result = asyncio.run(service.run_check(dry_run=True))

    body = json.loads(result["body"])
    assert body["dry"] is True
    assert body["new"] == 1
    assert not service.SEEN.is_seen("1")


def test_run_check_seed_marks_seen_without_notifying(tmp_path):
    from modules.media.sources.pahe.scanner import service

    posts = [_service_post("1", 2026, "Movie One")]

    async def fake_scrape(tags, *, needs_details=None, concurrency=8):
        return posts

    async def fake_bulk(batch):
        raise AssertionError("seed must not notify")

    with (
        patch.object(service.scraper, "scrape_latest_posts", fake_scrape),
        patch.object(service, "send_bulk_notifications", fake_bulk),
    ):
        result = asyncio.run(service.run_check(seed=True))

    body = json.loads(result["body"])
    assert body["seeded"] == 1
    assert service.SEEN.is_seen("1")


def test_run_check_skips_quarantined_source(tmp_path):
    import modules.media.source_health as sh
    from modules.media.sources.pahe.scanner import service

    for _ in range(3):
        sh.record("pahe", ok=False, error="down")

    async def fake_scrape(tags, *, needs_details=None, concurrency=8):
        raise AssertionError("quarantined source must not be scraped")

    with patch.object(service.scraper, "scrape_latest_posts", fake_scrape):
        result = asyncio.run(service.run_check())

    body = json.loads(result["body"])
    assert body["skipped"] is True


def test_run_check_lock_actually_blocks_a_second_checker():
    """Regression: media.py "imported" the lock with
    ``from modules.media.sources.pahe.scanner.main import _checking`` and then rebound that name
    locally, so the guard never engaged and two checks could both run."""
    from modules.media.sources.pahe.scanner import service

    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_scrape(tags, *, needs_details=None, concurrency=8):
        entered.set()
        await release.wait()
        return []

    async def run_first():
        return await service.run_check()

    async def scenario():
        with patch.object(service.scraper, "scrape_latest_posts", slow_scrape):
            first = asyncio.create_task(run_first())
            await entered.wait()
            # While the first check holds the lock, a second must be refused.
            second = await service.run_check()
            release.set()
            return await first, second

    first, second = asyncio.run(scenario())
    assert json.loads(first["body"])["checked"] == 0
    assert second["status"] == 429
    assert "in progress" in json.loads(second["body"])["error"]


def test_run_check_requires_a_token(tmp_path):
    from modules.media.sources.pahe.scanner import service

    with patch.dict("os.environ", {"PAHE_API_TOKEN": ""}):
        result = asyncio.run(service.run_check())

    assert result["status"] == 500
    assert "PAHE_API_TOKEN" in json.loads(result["body"])["error"]


def test_run_check_reports_feed_unavailable_and_arms_quarantine(tmp_path):
    """A dead upstream used to be reported as ``{"ok": true, "checked": 0}``
    with health green, so notifications stopped with no signal at all."""
    import modules.media.source_health as sh
    from modules.media.sources.pahe.scanner import service
    from modules.media.sources.pahe.scanner.scraper import FeedUnavailable

    async def dead(tags, *, needs_details=None, concurrency=8):
        raise FeedUnavailable("all 1 feed(s) unusable")

    with patch.object(service.scraper, "scrape_latest_posts", dead):
        result = asyncio.run(service.run_check())

    assert result["status"] == 503
    assert json.loads(result["body"])["error"] == "Feed unavailable"
    health = sh.snapshot()["pahe"]
    assert health["status"] == "degraded"
    assert health["consecutive_failures"] == 1


def test_run_check_survives_a_notification_batch_that_raises(tmp_path):
    """Delivery sits outside the scrape guard; an unexpected raise used to
    escape run_check and surface as an unhandled 500 from the endpoint."""
    from modules.media.sources.pahe.scanner import service

    async def one_feed(url):
        return [_service_post("1", 2026)]

    async def fake_details(url):
        return {"image": None, "synopsis": "", "downloads": []}

    async def boom(batch):
        raise RuntimeError("discord exploded")

    with (
        patch.object(service.scraper, "_safe_feed", one_feed),
        patch.object(service.scraper, "scrape_post_details", fake_details),
        patch.object(service, "send_bulk_notifications", boom),
    ):
        result = asyncio.run(service.run_check())

    assert result["status"] == 500
    assert json.loads(result["body"])["error"] == "Notification failed"


def test_run_check_notifies_a_duplicated_post_once(tmp_path):
    """A post tagged into two feeds used to be notified twice."""
    from modules.media.sources.pahe.scanner import service

    shared = _service_post("42", 2026, "Dup Movie")

    async def fake_scrape(tags, *, needs_details=None, concurrency=8):
        # scrape_latest_posts dedupes; simulate its post-dedup output.
        return [shared]

    sent: list[str] = []

    async def fake_bulk(batch):
        sent.extend(p.id for p in batch)
        return {
            "success": len(batch),
            "failed": 0,
            "results": [{"id": p.id, "ok": True} for p in batch],
        }

    with (
        patch.object(service.scraper, "scrape_latest_posts", fake_scrape),
        patch.object(service, "send_bulk_notifications", fake_bulk),
    ):
        result = asyncio.run(service.run_check())

    body = json.loads(result["body"])
    assert body["new"] == 1
    assert sent == ["42"]


def test_min_year_logs_instead_of_silently_disabling_the_gate(caplog):
    """A typo in PAHE_MIN_YEAR used to fall back to 0, which disables the gate
    and notifies every film pahe has ever back-filled."""
    import logging

    from modules.media.sources.pahe.scanner import service

    with (
        patch.dict("os.environ", {"PAHE_MIN_YEAR": "20x6"}),
        caplog.at_level(logging.ERROR, logger="media.pahe"),
    ):
        assert service.min_year() == 0
    assert "not a number" in caplog.text


def test_run_check_records_health_once_per_outcome(tmp_path):
    import modules.media.source_health as sh
    from modules.media.sources.pahe.scanner import service

    posts = [_service_post("1", 2026)]

    async def fake_scrape(tags, *, needs_details=None, concurrency=8):
        return posts

    async def fake_bulk(batch):
        return {"success": 0, "failed": 1, "results": [{"id": "1", "ok": False}]}

    with (
        patch.object(service.scraper, "scrape_latest_posts", fake_scrape),
        patch.object(service, "send_bulk_notifications", fake_bulk),
    ):
        asyncio.run(service.run_check())

    health = sh.snapshot()["pahe"]
    # One failure recorded, not two (the old code recorded from both the
    # wrapper and the inline check).
    assert health["consecutive_failures"] == 1
    assert health["status"] == "degraded"


# ---------------------------------------------------------------------------
# pahe.scanner.main — the router delegates to the service
# ---------------------------------------------------------------------------


def test_router_check_requires_token():
    from modules.media.sources.pahe.scanner import main as router_mod

    class Request:
        headers: dict = {}
        query_params: dict = {}

    with patch.dict("os.environ", {"PAHE_API_TOKEN": "sekret"}):
        assert not router_mod._require_token(Request())
        Request.headers = {"authorization": "Bearer sekret"}
        assert router_mod._require_token(Request())


def test_router_check_uses_the_shared_service():
    from modules.media.sources.pahe.scanner import main as router_mod

    class Request:
        headers = {"authorization": "Bearer test-token"}
        query_params: dict = {}

    async def fake_run(**kwargs):
        return {"source": "pahe", "status": 200, "body": json.dumps({"ok": True}), "rt_ms": 1}

    with patch.object(router_mod, "run_check", fake_run):
        response = asyncio.run(router_mod.check(Request()))

    assert response.status_code == 200
    assert json.loads(response.body)["ok"] is True


# ---------------------------------------------------------------------------
# scheduler — no circular import, interval measured from completion
# ---------------------------------------------------------------------------


def test_scheduler_tick_reports_without_raising():
    from modules.media.scheduler import _tick

    async def fake_run(**kwargs):
        return {"body": json.dumps({"ok": True, "new": 0})}

    with patch("modules.media.sources.pahe.scanner.service.run_check", fake_run):
        asyncio.run(_tick("pahe"))  # must not raise


def test_scheduler_supervised_loop_restarts_instead_of_dying():
    """An asyncio task that raises is never retried — the app would keep
    serving HTTP while scraping silently stopped forever. The supervised
    wrapper restarts the loop instead."""
    from modules.media.scheduler import _supervised_loop

    attempts = {"n": 0}

    async def exploding_loop():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("boom")
        raise asyncio.CancelledError

    async def fast_sleep(_):
        return None

    with (
        patch("modules.media.scheduler._loop", exploding_loop),
        patch("asyncio.sleep", fast_sleep),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(_supervised_loop())

    assert attempts["n"] == 3


def test_compile_all():
    import compileall

    assert compileall.compile_dir(".", quiet=1, force=True)


if __name__ == "__main__":
    print("regression self-check passed")
