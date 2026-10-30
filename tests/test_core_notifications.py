from core.notifications.outbox import compute_backoff


def test_backoff_within_cap():
    for attempt in range(1, 15):
        d = compute_backoff(attempt, base=5.0, cap=3600.0)
        assert 0.0 <= d <= 3600.0


def test_backoff_first_attempt_bounded_by_base():
    assert compute_backoff(1, base=5.0) <= 5.0


def test_backoff_respects_cap():
    assert compute_backoff(50, base=5.0, cap=100.0) <= 100.0


def test_backoff_grows_in_expectation():
    # full jitter is random; over many samples the mean should rise with attempt
    lo = sum(compute_backoff(1, base=5.0) for _ in range(200)) / 200
    hi = sum(compute_backoff(6, base=5.0) for _ in range(200)) / 200
    assert hi > lo
