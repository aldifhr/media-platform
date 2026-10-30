from modules.manhwa.domain.text import (
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
    # PRIMARY ASCII strip: é becomes a space, NOT transliterated to 'e'
    assert normalize_title_key("Pokémon Adventure") == "pok mon adventure"


def test_basic_title():
    assert normalize_title_key("Solo Leveling") == "solo leveling"


def test_empty():
    assert normalize_title_key("") == ""


def test_cjk_hash_fallback():
    k = normalize_title_key("王者天下")
    assert k.startswith("t") and len(k) == 16  # 't' + 15 hex


def test_slugify_deslugify_roundtrip():
    slug = slugify_title_key("Eternally Regressing Knight")
    assert slug == "eternally-regressing-knight"
    assert deslugify_title_key(slug) == "eternally regressing knight"


def test_is_uuid_title_key():
    assert is_uuid_title_key("123e4567-e89b-12d3-a456-426614174000") is True
    assert is_uuid_title_key("solo-leveling") is False
    assert is_uuid_title_key("") is False


def test_extract_uuid():
    u = "123e4567-e89b-12d3-a456-426614174000"
    assert extract_uuid(f"https://x/series/{u}") == u
    assert extract_uuid("no uuid here") is None
    assert extract_uuid(None) is None


def test_escape_like():
    assert escape_like("100%_x") == r"100\%\_x"
    assert escape_like("") == ""


def test_strip_html():
    assert strip_html("<p>Hi &amp; bye</p>") == "Hi & bye"
    assert strip_html(None) == ""


def test_slug_from_series_url():
    assert slug_from_series_url("https://x/series/foo/") == "foo"
    assert slug_from_series_url("") == ""
