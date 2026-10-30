from modules.manhwa.core.text import (
    deslugify_title_key,
    escape_like,
    extract_uuid,
    is_uuid_title_key,
    normalize_title_key,
    slug_from_series_url,
    slugify_title_key,
    strip_html,
)


def test_ascii_first_rule_not_transliterated():
    assert normalize_title_key("Pokémon Adventure") == "pok mon adventure"


def test_basic_and_empty():
    assert normalize_title_key("Solo Leveling") == "solo leveling"
    assert normalize_title_key("") == ""


def test_cjk_hash_fallback():
    k = normalize_title_key("王者天下")
    assert k.startswith("t") and len(k) == 16


def test_slugify_deslugify_roundtrip():
    slug = slugify_title_key("Eternally Regressing Knight")
    assert slug == "eternally-regressing-knight"
    assert deslugify_title_key(slug) == "eternally regressing knight"


def test_uuid_and_extract():
    u = "123e4567-e89b-12d3-a456-426614174000"
    assert is_uuid_title_key(u) is True
    assert is_uuid_title_key("solo") is False
    assert extract_uuid(f"https://x/series/{u}") == u
    assert extract_uuid("none") is None


def test_escape_like_strip_html_slug():
    assert escape_like("100%_x") == r"100\%\_x"
    assert strip_html("<p>Hi &amp; bye</p>") == "Hi & bye"
    assert slug_from_series_url("https://x/series/foo/") == "foo"
