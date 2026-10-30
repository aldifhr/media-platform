from modules.manhwa.domain.rating import normalize_rating


def test_none_and_invalid():
    assert normalize_rating(None) is None
    assert normalize_rating("") is None
    assert normalize_rating("bad") is None


def test_zero_and_negative():
    assert normalize_rating(0) is None
    assert normalize_rating(-3) is None


def test_percent_scale_divided():
    assert normalize_rating(85) == 8.5
    assert normalize_rating(15) == 1.5


def test_below_one_none():
    assert normalize_rating(0.5) is None


def test_clamp_and_round():
    assert normalize_rating(10) == 10.0
    assert normalize_rating(7.456) == 7.46
    assert normalize_rating(100) == 10.0
