"""Pahe.ink scraper.

Fetches the pahe.ink realtime feed (or per-tag feeds), extracts post metadata
plus download links, and returns normalized :class:`Post` objects.

Two invariants the rest of the service depends on:

* ``scrape_latest_posts`` never raises because of one bad post — a single
  unparseable page degrades that post to "no details", it does not abort the
  whole check.
* ``scrape_post_details`` always returns a dict with ``image``, ``synopsis``
  and ``downloads`` keys, even when the page has no download box at all.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from modules.media.common.log import get_logger

log = get_logger("scraper")


@dataclass
class Post:
    id: str
    slug: str
    title: str
    url: str
    description: str
    pub_date: str
    genres: list[str] = field(default_factory=list)
    qualities: list[str] = field(default_factory=list)
    year: int | None = None
    image: str | None = None
    synopsis: str = ""
    downloads: list[dict[str, Any]] = field(default_factory=list)


class FeedUnavailable(Exception):
    """Every configured feed failed to fetch or parse.

    Distinct from an empty feed: an empty feed means "nothing new", this means
    "we could not look". Letting the second masquerade as the first is how a
    scraper goes silent with a green health check.
    """


HTTP_TIMEOUT = 15.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
FEED_BASE = "https://pahe.ink"
DETAIL_CONCURRENCY = 8

# Compile-once patterns. These used to be rebuilt inside the per-post loop,
# which is ~16 re.compile calls per check for no reason.
_YEAR_CAT = re.compile(r"\d{4}")
_QUALITY_CAT = re.compile(r"(480p|720p|1080p|2160p)")
_TITLE_YEAR = re.compile(r"\((\d{4})\)")
_NON_QUALITY_CAT = frozenset({"1080p", "720p", "480p", "2160p", "x265", "x264", "dv", "HDR"})
_QUALITY_TOKEN = re.compile(r"(480p|720p|1080p|2160p)[^\n<]*", re.I)
_SIZE = re.compile(r"\s*\|\s*[\d.]+\s*(MB|GB|TB)", re.I)
_EPISODE = re.compile(r"episode\s*\d+", re.I)
_CODEC = re.compile(r"(720p|1080p|2160p|x264|x265|10bit|h264|h265|hevc)", re.I)
_QUALITY_PICK = re.compile(r"(2160p|1080p|720p|480p)\s*(x264|x265|10bit|h264|h265|hevc)?", re.I)
_NOTIFY_QUALITIES = ("720p", "1080p", "2160p")
_METASCORE = re.compile(r"Metascore:\s*\S+\s*(.*)", re.S)
_DIRECTOR = re.compile(r"\s*Director:.*$", re.S)
_META_LEAK = re.compile(r"(?i)(actors?:|rating:|metascore:|genre:)")
_WHITESPACE = re.compile(r"\s+")

# One shared client per event loop: connection pooling + keep-alive across
# every feed + post page fetched in a single scrape. Creating an AsyncClient
# per request paid TLS+TCP handshake on every one of the ~17 requests a check
# makes — that was the dominant cost of a /check.
#
# The client is bound to the loop that created it. httpx raises
# "Event loop is closed" the moment a different loop touches it, so the loop
# identity is tracked and a foreign client is discarded rather than reused —
# otherwise a process that ever runs a second loop (a test client, a reload)
# silently fails every request until restart.
_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _http() -> httpx.AsyncClient:
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is not None and (_client.is_closed or _client_loop is not loop):
        _client = None
        _client_loop = None
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        _client_loop = loop
    return _client


async def aclose_http() -> None:
    """Close the shared client. Call from the app's shutdown lifespan."""
    global _client, _client_loop
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
    _client_loop = None


async def _get(url: str) -> str:
    response = await _http().get(url)
    response.raise_for_status()
    return response.text


_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _text(element: ET.Element, tag: str) -> str:
    """Text of ``<tag>``, in RSS (bare) or Atom (namespaced) form."""
    found = element.find(tag)
    if found is None:
        found = element.find(f"{_ATOM_NS}{tag}")
    return (found.text or "").strip() if found is not None else ""


def _post_id(guid: str, link: str) -> str:
    """Derive a stable post id.

    pahe emits ``?p=<id>`` guids, but the id is the dedup key for the whole
    service, so fall back to the URL slug rather than storing the raw guid
    (which would make every id unique and defeat dedup entirely).
    """
    if guid and "?p=" in guid:
        candidate = guid.split("?p=", 1)[1].strip().rstrip("/")
        if candidate:
            return candidate
    return link.rstrip("/")


def _slug(link: str) -> str:
    return link.replace(f"{FEED_BASE}/", "").rstrip("/")


def _entry_link(entry: ET.Element) -> str:
    """Link from an RSS ``<link>`` or an Atom ``<link href=...>``."""
    direct = _text(entry, "link")
    if direct:
        return direct
    for link in entry.iter(f"{_ATOM_NS}link"):
        href = (link.get("href") or "").strip()
        if href:
            return href
    return ""


def _categories(entry: ET.Element) -> list[str]:
    """Category labels from RSS ``<category>text</category>`` or Atom
    ``<category term="..."/>``."""
    labels: list[str] = []
    for tag in ("category", f"{_ATOM_NS}category"):
        for category in entry.iter(tag):
            text = (category.text or "").strip()
            if text:
                labels.append(text)
                continue
            term = (category.get("term") or "").strip()
            if term:
                labels.append(term)
    return labels


def _entry_id(entry: ET.Element, link: str) -> str:
    """Stable id from an RSS ``<guid>`` or an Atom ``<id>``."""
    return _post_id(_text(entry, "guid") or _text(entry, "id"), link)


def _entry_date(entry: ET.Element) -> str:
    return _text(entry, "pubDate") or _text(entry, "published") or _text(entry, "updated")


def _feed_entries(root: ET.Element) -> list[ET.Element]:
    """RSS ``<item>`` elements, or Atom ``<entry>`` elements.

    pahe.ink serves RSS today, but ``root.iter("item")`` silently returns
    nothing for an Atom feed — a format switch upstream would look exactly
    like "no new posts" rather than "we cannot read this".
    """
    entries = list(root.iter("item"))
    if entries:
        return entries
    return list(root.iter(f"{_ATOM_NS}entry"))


async def scrape_feed(url: str) -> list[Post]:
    """Parse an RSS/Atom feed from modules.media.sources.pahe.ink and return posts."""
    xml = await _get(url)
    root = ET.fromstring(xml)
    posts: list[Post] = []
    for item in _feed_entries(root):
        title = _text(item, "title")
        link = _entry_link(item)
        description = _text(item, "description")
        pub_date = _entry_date(item)

        genres: list[str] = []
        qualities: list[str] = []
        year: int | None = None
        for text in _categories(item):
            if _YEAR_CAT.fullmatch(text):
                year = int(text)
            elif _QUALITY_CAT.fullmatch(text):
                qualities.append(text)
            elif text not in _NON_QUALITY_CAT:
                genres.append(text)

        if year is None:
            match = _TITLE_YEAR.search(title)
            if match:
                year = int(match.group(1))

        posts.append(
            Post(
                id=_entry_id(item, link),
                slug=_slug(link),
                title=title,
                url=link,
                description=description,
                pub_date=pub_date,
                genres=genres,
                qualities=qualities,
                year=year,
            )
        )
    return posts


def _episode_marker(box) -> str:
    """Episode label for a download box, or "" for a movie."""
    for element in box.descendants:
        name = getattr(element, "name", None)
        if name == "b":
            match = _EPISODE.search(element.get_text(strip=True))
            if match:
                return match.group(0)
        elif name is None:
            match = _EPISODE.search(str(element).strip())
            if match:
                return match.group(0)
    return ""


def _quality_from(element) -> str:
    """Quality label from a <b>/<strong> or a bare text node (Layout B)."""
    name = getattr(element, "name", None)
    if name in ("b", "strong"):
        text = element.get_text(strip=True)
        if not _CODEC.search(text):
            return ""
        picked = _QUALITY_PICK.search(text)
        return picked.group(0).strip() if picked else _SIZE.sub("", text).strip()
    if name is None:
        parent = getattr(element, "parent", None)
        parent_name = getattr(parent, "name", None) if parent else None
        if parent_name in (None, "div", "p", "span", "i"):
            match = _QUALITY_TOKEN.search(str(element).strip())
            if match:
                return _SIZE.sub("", match.group(0)).strip()
    return ""


def _parse_downloads(soup: BeautifulSoup) -> list[dict]:
    """Walk every .box.download block and collect shortc-button links.

    pahe ships two quality-label layouts and only one used to be handled, so
    the 720p/1080p/2160p filter dropped every link for the other layout.
    """
    downloads: list[dict] = []
    seen: set[str] = set()
    boxes = soup.find_all("div", class_="box download") or soup.find_all(class_="download")
    for box in boxes:
        episode = _episode_marker(box)
        current_quality = ""
        for element in box.descendants:
            name = getattr(element, "name", None)
            if name in ("b", "strong", None):
                found = _quality_from(element)
                if found:
                    current_quality = found
            elif name == "a" and "shortc-button" in (element.get("class") or []):
                href = element.get("href", "")
                if not href or not current_quality:
                    continue
                quality = f"{episode} • {current_quality}" if episode else current_quality
                key = f"{quality}|{href}"
                if key in seen:
                    continue
                seen.add(key)
                downloads.append(
                    {"quality": quality, "url": href, "label": element.get_text(strip=True)}
                )

    return [
        d for d in downloads if d["quality"].split("•")[-1].strip().startswith(_NOTIFY_QUALITIES)
    ]


def _extract_synopsis(article) -> str:
    """Prefer the dedicated teaser element; fall back to pre-download text."""
    teaser = article.find(class_="imdbwp__teaser")
    if teaser:
        synopsis = teaser.get_text(" ", strip=True)
    else:
        article_html = str(article)
        box_index = article_html.find("box download")
        body_html = article_html[:box_index] if box_index > 0 else article_html
        body_soup = BeautifulSoup(body_html, "html.parser")
        for tag in body_soup(["script", "style"]):
            tag.decompose()
        body_text = _WHITESPACE.sub(" ", body_soup.get_text()).strip()
        match = _METASCORE.match(body_text)
        synopsis = (match.group(1) if match else body_text).strip()
        synopsis = _DIRECTOR.sub("", synopsis).strip()
    # Strip any leftover metadata leak (rating/actors/genre lines).
    synopsis = _META_LEAK.split(synopsis)[0].strip()
    return synopsis[:400] if len(synopsis) > 40 else synopsis


def _extract_image(article, page_url: str) -> str | None:
    """Absolute image URL, or None.

    Discord rejects an embed whose thumbnail URL is not absolute, and pahe
    sometimes emits a protocol-relative or root-relative src.
    """
    tag = article.find("img")
    src = (tag.get("src") or "").strip() if tag else ""
    if not src or src.startswith("data:"):
        return None
    absolute = urljoin(page_url, src)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return absolute


async def scrape_post_details(post_url: str) -> dict:
    """Fetch one post page and extract synopsis, image and downloads.

    Always returns all three keys — a page with no download box yields an
    empty list rather than raising (the old code left ``filtered`` unbound in
    exactly that case, which turned one odd post into a 500 for the whole
    check).
    """
    empty = {"image": None, "synopsis": "", "downloads": []}
    try:
        html = await _get(post_url)
    except Exception as exc:
        log.warning("post page fetch failed url=%s err=%s", post_url, type(exc).__name__)
        return empty

    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    if not article:
        return empty

    return {
        "image": _extract_image(article, post_url),
        "synopsis": _extract_synopsis(article),
        "downloads": _parse_downloads(soup),
    }


async def _with_details(post: Post, semaphore: asyncio.Semaphore) -> Post:
    async with semaphore:
        try:
            details = await scrape_post_details(post.url)
        except Exception as exc:  # never let one post abort the batch
            log.warning("details failed id=%s err=%s", post.id, type(exc).__name__)
            return post
        post.image = details["image"]
        post.synopsis = details["synopsis"]
        post.downloads = details["downloads"]
        return post


async def scrape_latest_posts(
    tags: list[str],
    *,
    needs_details: Callable[[Post], bool] | None = None,
    concurrency: int = DETAIL_CONCURRENCY,
) -> list[Post]:
    """Scrape the configured feeds and enrich each post with its page details.

    ``needs_details`` lets the caller skip the detail fetch for posts it has
    already processed. That is the single biggest win available here: a check
    pulls ~16 posts and historically fetched all 16 pages even when nothing
    was new, which is ~16 HTTP round-trips every 5 minutes to re-learn URLs
    already stored. Pass a predicate and only the posts that matter pay for a
    page.

    Raises :class:`FeedUnavailable` when *every* configured feed failed. A
    single dead tag feed is tolerated (the others still produce posts), but a
    total failure must not look like "no new posts" — that is how a scraper
    goes quiet for weeks with a green health check.
    """
    feeds = [f"{FEED_BASE}/tag/{tag}/feed/" for tag in tags] if tags else [f"{FEED_BASE}/feed/"]

    results = await asyncio.gather(*[_safe_feed(url) for url in feeds])
    # A feed is unusable if it raised OR parsed to nothing. pahe's realtime
    # feed always carries items, so zero entries means "we could not read
    # this", not "nothing is new" — an HTML error page that happens to be
    # well-formed XML lands here too.
    unusable = [url for url, batch in zip(feeds, results, strict=True) if not batch]
    if unusable and len(unusable) == len(feeds):
        raise FeedUnavailable(f"all {len(feeds)} feed(s) unusable: {', '.join(unusable)}")
    for url in unusable:
        log.warning("feed unusable, continuing with the rest url=%s", url)

    # A post tagged into two feeds appears twice; without this the same title
    # is notified twice and its page is fetched twice.
    posts: list[Post] = []
    seen_ids: set[str] = set()
    for batch in results:
        for post in batch or []:
            if post.id in seen_ids:
                continue
            seen_ids.add(post.id)
            posts.append(post)

    wanted = posts if needs_details is None else [p for p in posts if needs_details(p)]

    if not wanted:
        return posts

    semaphore = asyncio.Semaphore(concurrency)
    enriched = await asyncio.gather(*[_with_details(post, semaphore) for post in wanted])
    by_id = {post.id: post for post in enriched}
    return [by_id.get(post.id, post) for post in posts]


async def _safe_feed(url: str) -> list[Post] | None:
    """Fetch and parse one feed.

    Returns ``None`` when the feed could not be read or parsed, so the caller
    can tell "upstream is broken" apart from "upstream has nothing new".
    Swallowing the error and returning ``[]`` made a dead or reformatted
    feed indistinguishable from a quiet one — the check reported
    ``{"ok": true, "checked": 0}``, health stayed green, and notifications
    stopped with no signal at all.
    """
    try:
        return await scrape_feed(url)
    except Exception as exc:
        log.warning("feed failed url=%s err=%s: %s", url, type(exc).__name__, exc)
        return None


def content_hash(post: Post) -> str:
    """MD5 of download quality|url lines (mirrors the Node/legacy logic)."""
    payload = "\n".join(f"{d.get('quality', '')}|{d.get('url', '')}" for d in post.downloads)
    return hashlib.md5(payload.encode()).hexdigest()
