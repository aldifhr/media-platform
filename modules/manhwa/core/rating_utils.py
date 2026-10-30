"""Centralized rating normalization for manhwa-backend.

All scrapers + cron should import normalize_rating() from here.
Contract: return float in [1.0, 10.0], or None if missing/invalid.
"""
from __future__ import annotations


def normalize_rating(v) -> float | None:
    """Normalize any rating input to 1-10 scale.

    - None / empty / non-numeric → None
    - 0 / negative → None
    - > 10 (percent scale, e.g. 85) → /10
    - < 1 (after percent check) → None
    - Clamped to [1.0, 10.0], rounded to 2 decimals.
    """
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    if f <= 0:
        return None
    if f > 10.0:
        f = f / 10.0
    if f < 1.0:
        return None
    return round(min(10.0, max(1.0, f)), 2)
