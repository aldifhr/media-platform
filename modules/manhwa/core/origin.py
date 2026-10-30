"""Shared origin (country) normalization.

Single source of truth for converting source-type strings
(manhwa/manga/manhua) or country codes (kr/jp/cn) into a canonical
upper-case country code: KR / CN / JP.

This prevents the recurring bug where `origin` ended up as a *type*
(manhwa/manga/manhua) instead of a *country* (KR/CN/JP) in the RSS feed
and downstream tables.
"""
from __future__ import annotations

import re

# Source "type" -> country code
TYPE_TO_CC = {
    "manhwa": "KR",
    "manga": "JP",
    "manhua": "CN",
}

# Country code (any case) -> canonical upper-case code
COUNTRY_TO_CC = {
    "KR": "KR",
    "JP": "JP",
    "CN": "CN",
    "kr": "KR",
    "jp": "JP",
    "cn": "CN",
    "korea": "KR",
    "japan": "JP",
    "china": "CN",
    "korean": "KR",
    "japanese": "JP",
    "chinese": "CN",
}

VALID_CC = {"KR", "CN", "JP"}

# In-memory normalize_origin cache bounded to 1024 entries.
# origin strings are short and values are stable country codes, so this
# elimiates repeated strip/lower/dict-lookup work across hot paths
# (RSS feed, collect, enrich) without changing any semantics.
_NORMALIZE_ORIGIN_CACHE: dict[str, str] = {}
_NORMALIZE_ORIGIN_CACHE_MAX = 1024


def normalize_origin(raw) -> str:
    """Return canonical country code (KR/CN/JP) or '' if unknown.

    Accepts: country code (kr/jp/cn/KR), type (manhwa/manga/manhua),
    or full name (korean/japanese/chinese).
    """
    cache_key = str(raw) if raw is not None else ""
    if not cache_key:
        return ""
    try:
        return _NORMALIZE_ORIGIN_CACHE[cache_key]
    except KeyError:
        pass
    s = cache_key.strip().lower()
    if not s:
        _NORMALIZE_ORIGIN_CACHE[cache_key] = ""
        return ""
    if s in COUNTRY_TO_CC:
        out = COUNTRY_TO_CC[s]
    elif s in TYPE_TO_CC:
        out = TYPE_TO_CC[s]
    else:
        out = ""
        for tok in re.split(r"[\s,/+\[\]]+", s):
            tok = tok.strip("'\"")
            if not tok:
                continue
            if tok in TYPE_TO_CC:
                out = TYPE_TO_CC[tok]
                break
            if tok in COUNTRY_TO_CC:
                out = COUNTRY_TO_CC[tok]
                break
    _NORMALIZE_ORIGIN_CACHE[cache_key] = out
    if len(_NORMALIZE_ORIGIN_CACHE) > _NORMALIZE_ORIGIN_CACHE_MAX:
        oldest = next(iter(_NORMALIZE_ORIGIN_CACHE))
        _NORMALIZE_ORIGIN_CACHE.pop(oldest, None)
    return out


def is_valid_country_code(value: str) -> bool:
    return str(value or "").upper() in VALID_CC


# Country code -> source "type" (manhwa/manga/manhua)
CC_TO_TYPE = {
    "KR": "manhwa",
    "CN": "manhua",
    "JP": "manga",
}


def country_to_type(value) -> str:
    """Map a country code (kr/KR) or type string to its canonical type.

    Returns '' when the input is not a recognized country. Accepts the same
    inputs as ``normalize_origin`` so callers can chain them.
    """
    cc = normalize_origin(value)
    return CC_TO_TYPE.get(cc, "")
