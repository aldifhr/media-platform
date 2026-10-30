from core.events import EVENT_VERSION, Event, new_event


def test_new_event_fields():
    e = new_event("manhwa.chapter.released", "ikiru", {"chapter": 1})
    assert e.event_type == "manhwa.chapter.released"
    assert e.source == "ikiru"
    assert e.version == EVENT_VERSION
    assert e.payload == {"chapter": 1}
    assert e.event_id
    assert e.occurred_at


def test_event_ids_unique():
    a = new_event("media.release.detected", "pahe", {})
    b = new_event("media.release.detected", "pahe", {})
    assert a.event_id != b.event_id


def test_event_is_serializable():
    e = new_event("media.release.detected", "pahe", {"title": "x"})
    assert Event.model_validate(e.model_dump()) == e
