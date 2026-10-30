"""The freshness rule for a pahe post — one definition, two callers.

``media.py`` and ``pahe/scanner/main.py`` each used to decide "is this post
new?" on their own, and they disagreed. The standalone router kept a hardcoded
``p.year is not None and p.year >= MIN_YEAR``, which silently dropped every
serial (pahe marks those ``[Episode N Added]`` and gives them no release
year) — the exact regression ``test_new_post_rule`` was written for, still
live on the port-3100 service. ``media.py`` had the fixed rule.

Both now call :func:`is_new_post`.
"""

from __future__ import annotations

import re
from typing import Protocol


class _Post(Protocol):
    id: str
    year: int | None


class _Seen(Protocol):
    def is_seen(self, key: str) -> bool: ...


# A post is a serial if pahe gave it no release year, OR if the title says so.
# The title check matters: pahe sometimes emits a year for a series
# ("Show (2025) Season 2"), and a year-gated rule would then drop a brand-new
# episode of a 2024 show. Freshness for a serial is the episode, never the
# release year.
_SERIAL_TITLE = re.compile(
    r"""season\s*\d+            # "Season 2"
        | episode\s*\d+         # "Episode 7 Added"
        | \b s\d{1,2} (?! e )   # "S2" / "S02", but not "S2E7" (covered below)
        | \b s\d{1,2} \s* e\d{1,2} \b   # "S02E07"
        | \[episode\b           # "[Episode ..."
    """,
    re.I | re.X,
)


def is_serial(post: _Post) -> bool:
    """True when a post is a serial/episode release rather than a film."""
    if post.year is None:
        return True
    title = getattr(post, "title", "") or ""
    return bool(_SERIAL_TITLE.search(title))


def is_new_post(post: _Post, seen: _Seen, min_year: int) -> bool:
    """Decide whether a scraped post should be notified.

    Freshness on pahe is the post id, not the release year: pahe routinely
    back-fills its catalogue with decades-old films, so a ``year >= N`` gate on
    its own would either mute the source entirely or spam old titles. Two
    content kinds, two rules:

    * serials are always fresh — ``[Episode N Added]`` is new content however
      old the show is, and they bypass ``PAHE_MIN_YEAR`` entirely;
    * movies carry a release year, so ``PAHE_MIN_YEAR`` applies to them.

    ``min_year == 0`` disables the movie gate.
    """
    if seen.is_seen(post.id):
        return False
    if is_serial(post):
        return True
    return _movie_passes_gate(post, min_year)


def is_recheck_candidate(post: _Post, seen: _Seen, min_year: int) -> bool:
    """Whether a seen post's download links should be re-examined.

    Serials are included: a new episode changes the download block, and that
    is exactly the change worth re-notifying. Only the movie year gate still
    applies, and only to films.
    """
    if not is_serial(post) and post.year is not None and post.year < min_year:
        return False
    return seen.is_seen(post.id)


def _movie_passes_gate(post: _Post, min_year: int) -> bool:
    """Year gate for films only. ``min_year == 0`` disables it."""
    if not min_year:
        return True
    return post.year is not None and post.year >= min_year
