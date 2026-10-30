from modules.manhwa.core.fcfs import (
    fcfs_key,
    fcfs_key_for_item,
    normalize_chapter,
    normalize_chapter_num,
    normalize_title,
    parse_chapter_number,
)
from modules.manhwa.core.rating_utils import normalize_rating


def test_normalize_chapter_cases():
    assert normalize_chapter("12.50") == "12.5"
    assert normalize_chapter("160-2") == "160.2"
    assert normalize_chapter("012") == "12"
    assert normalize_chapter("12.00") == "12"
    assert normalize_chapter("OVA") == "OVA"
    assert normalize_chapter(None) == ""
    assert normalize_chapter(0) == "0"  # load-bearing: 0 stays "0"


def test_fcfs_key_and_item():
    assert fcfs_key("Solo Leveling", "12.50") == "solo-leveling#12.5"
    item = {"title_key": "solo-leveling", "chapter": "12.50"}
    assert fcfs_key_for_item(item) == "solo-leveling#12.5"
    assert fcfs_key("X", 0) == fcfs_key("X", "0")  # number vs string dedupe


def test_parse_and_num():
    assert parse_chapter_number("Ch 160") == 160.0
    assert parse_chapter_number("OVA") is None
    assert normalize_chapter_num("12.50") == "12.5"
    assert normalize_title("Eternally Regressing Knight") == "eternally-regressing-knight"


def test_normalize_rating():
    assert normalize_rating(85) == 8.5
    assert normalize_rating(0) is None
    assert normalize_rating(0.5) is None
    assert normalize_rating(7.456) == 7.46
    assert normalize_rating("bad") is None
