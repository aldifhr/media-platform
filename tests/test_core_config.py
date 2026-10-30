from core.config import Settings, get_settings


def test_settings_defaults():
    s = get_settings()
    assert s.pahe_min_year == 2026
    assert s.pahe_check_interval_minutes == 30
    assert s.discord_gateway_enabled is False


def test_pahe_allowed_tag_set_parsing():
    s = Settings(pahe_allowed_tags="Horror, Action ,, Drama")
    assert s.pahe_allowed_tag_set == {"horror", "action", "drama"}


def test_empty_tags():
    assert Settings(pahe_allowed_tags="").pahe_allowed_tag_set == set()
