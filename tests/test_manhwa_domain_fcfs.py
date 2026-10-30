from modules.manhwa.domain.fcfs import (
    fcfs_key,
    fcfs_key_for_item,
    normalize_chapter,
    normalize_chapter_num,
    normalize_title,
    parse_chapter_number,
)


def test_normalize_chapter_cases():
    assert normalize_chapter("12.50") == "12.5"
    assert normalize_chapter("160-2") == "160.2"
    assert normalize_chapter("012") == "12"
    assert normalize_chapter("12.00") == "12"
    assert normalize_chapter("12.05") == "12.05"
    assert normalize_chapter("OVA") == "OVA"  # non-numeric preserved
    assert normalize_chapter(None) == ""
    assert normalize_chapter("") == ""


def test_normalize_chapter_zero_stays_zero():
    # the load-bearing trap: numeric 0 and string "0" must match
    assert normalize_chapter(0) == "0"
    assert normalize_chapter("0") == "0"


def test_fcfs_key():
    assert fcfs_key("Solo Leveling", "12.50") == "solo-leveling#12.5"


def test_fcfs_key_for_item_dash_to_space():
    item = {"title_key": "solo-leveling", "chapter": "12.50"}
    assert fcfs_key_for_item(item) == "solo-leveling#12.5"


def test_fcfs_key_number_vs_string_dedupe():
    # same chapter as number vs string must produce the SAME key
    assert fcfs_key("Solo Leveling", 0) == fcfs_key("Solo Leveling", "0")


def test_normalize_title():
    assert normalize_title("Eternally Regressing Knight") == "eternally-regressing-knight"


def test_parse_chapter_number():
    assert parse_chapter_number("12.5") == 12.5
    assert parse_chapter_number("Ch 160") == 160.0
    assert parse_chapter_number(7) == 7.0
    assert parse_chapter_number("OVA") is None
    assert parse_chapter_number(None) is None


def test_normalize_chapter_num():
    assert normalize_chapter_num("12.50") == "12.5"
    assert normalize_chapter_num(12.5) == "12.5"
    assert normalize_chapter_num("abc") == "abc"
