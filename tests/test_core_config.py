from core.config import Settings


def test_settings_defaults(monkeypatch):
    # isolate from repo .env AND any leaked OS env (e.g. a sourced .env)
    for k in ("PAHE_MIN_YEAR", "PAHE_CHECK_INTERVAL_MINUTES", "DISCORD_GATEWAY_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)  # pure defaults
    assert s.pahe_min_year == 2026
    assert s.pahe_check_interval_minutes == 30
    assert s.discord_gateway_enabled is False


def test_pahe_allowed_tag_set_parsing():
    s = Settings(pahe_allowed_tags="Horror, Action ,, Drama")
    assert s.pahe_allowed_tag_set == {"horror", "action", "drama"}


def test_empty_tags():
    assert Settings(pahe_allowed_tags="").pahe_allowed_tag_set == set()
