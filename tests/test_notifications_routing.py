from core.events import new_event
from core.notifications.routing import resolve_targets, resolve_thread


def test_resolve_targets_by_module():
    e = new_event("manhwa.chapter.released", "ikiru", {})
    subs = [
        {"guild_id": 1, "channel_id": 10, "module": "manhwa", "category": None, "enabled": True},
        {"guild_id": 2, "channel_id": 20, "module": "media", "category": None, "enabled": True},
    ]
    assert [s["guild_id"] for s in resolve_targets(subs, e)] == [1]


def test_resolve_targets_category_filter_and_disabled():
    e = new_event("media.release.detected", "pahe", {"category": "pahe"})
    subs = [
        {"guild_id": 1, "channel_id": 10, "module": "media", "category": "pahe", "enabled": True},
        {"guild_id": 2, "channel_id": 20, "module": "media", "category": "korea", "enabled": True},
        {"guild_id": 3, "channel_id": 30, "module": "media", "category": None, "enabled": True},
        {"guild_id": 4, "channel_id": 40, "module": "media", "category": "pahe", "enabled": False},
    ]
    assert sorted(s["guild_id"] for s in resolve_targets(subs, e)) == [1, 3]


def test_resolve_thread_first_match_by_priority():
    e = new_event("media.release.detected", "pahe", {"year": 2026, "genre": "horror"})
    routes = [
        {
            "event_type": "media.release.detected",
            "rule": {"genre": "horror"},
            "thread_id": 200,
            "priority": 50,
            "enabled": True,
        },
        {
            "event_type": "media.release.detected",
            "rule": {"year": 2026},
            "thread_id": 100,
            "priority": 10,
            "enabled": True,
        },
        {
            "event_type": "media.release.detected",
            "rule": {},
            "thread_id": 999,
            "priority": 99,
            "enabled": False,
        },
    ]
    assert resolve_thread(routes, e) == 100


def test_resolve_thread_no_match():
    e = new_event("media.release.detected", "pahe", {"year": 2020})
    routes = [
        {
            "event_type": "media.release.detected",
            "rule": {"year": 2026},
            "thread_id": 100,
            "priority": 10,
            "enabled": True,
        }
    ]
    assert resolve_thread(routes, e) is None
