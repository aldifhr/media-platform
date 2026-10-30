"""Drakorasia — drama scraper for https://drakorasia.space/.

The site publishes its list as schema.org JSON-LD, but the node shape differs
per page: the homepage wraps entries in a ``Blog`` node (``blogPost``) while a
category page wraps the same ``BlogPosting`` shape in a ``CollectionPage`` node
(``hasPart``). Both are CDATA-wrapped, so the block is unwrapped before the
JSON object is extracted.

Two categories are tracked, each with its own Discord thread: ``drama-korea``
and ``drama-china``.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from modules.media.common.http import simple_get

BASE = "https://drakorasia.space"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Category slug -> listing URL (sorted latest first).
CATEGORIES = {
    "korea": f"{BASE}/category/drama-korea/?cat=drama-korea&genre=&status=&year=&sort=latest",
    "china": f"{BASE}/category/drama-china/?cat=drama-china&genre=&status=&year=&sort=latest",
}


def _norm_url(url: str) -> str:
    """Canonicalise drakorasia.{work,art,co} -> .space."""
    if not url:
        return url
    return re.sub(r"drakorasia\.(work|art|co)(?=/|$)", "drakorasia.space", url)


def _norm_img(url: str) -> str:
    """Rewrite the dead image CDN hosts onto the main host."""
    if not url:
        return url
    m = re.match(r"https?://img\.drakorasia\.(?:work|art|co)(/.*)$", url)
    if m:
        path = m.group(1)
        if not path.startswith("/wp-content/uploads/"):
            path = "/wp-content/uploads" + path
        return "https://drakorasia.space" + path
    return _norm_url(url)


def _slug_from_url(url: str) -> str:
    if not url:
        return ""
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def fetch_homepage() -> str:
    return simple_get(BASE + "/", timeout=25, headers={"User-Agent": UA})


def fetch_category(cat: str) -> str:
    """Fetch one category listing page by key ("korea" / "china")."""
    url = CATEGORIES.get(cat)
    if not url:
        raise ValueError(f"unknown drakorasia category: {cat!r}")
    return simple_get(url, timeout=25, headers={"User-Agent": UA})


def fetch_detail(url: str) -> str:
    """Fetch one drama detail page by absolute or root-relative url."""
    if url.startswith("/"):
        url = BASE + url
    return simple_get(url, timeout=25, headers={"User-Agent": UA})


def _extract_posts(html: str) -> list[dict]:
    """Pull BlogPosting entries out of the JSON-LD script blocks.

    Handles both container shapes: ``Blog.blogPost`` (homepage) and
    ``CollectionPage.hasPart`` (category pages).
    """
    raw_blocks = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.S,
    )
    for b in raw_blocks:
        s = re.sub(r"^\s*/\*<!\[CDATA\[\*/", "", b.strip(), flags=re.S)
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            continue
        try:
            doc = json.loads(m.group(0))
        except Exception:
            continue
        for o in doc if isinstance(doc, list) else [doc]:
            if not isinstance(o, dict):
                continue
            if o.get("@type") == "Blog":
                posts = o.get("blogPost") or []
            elif o.get("@type") == "CollectionPage":
                posts = o.get("hasPart") or []
            else:
                continue
            if isinstance(posts, dict):
                posts = [posts]
            posts = [
                p
                for p in posts
                if isinstance(p, dict) and p.get("@type") in (None, "BlogPosting")
            ]
            if posts:
                return posts
    return []


def _to_item(p: dict, category: str) -> dict | None:
    mep = p.get("mainEntityOfPage")
    url = _norm_url(str((mep.get("@id") if isinstance(mep, dict) else p.get("url")) or ""))
    slug = _slug_from_url(url)
    if not url or not slug:
        return None
    img = p.get("image")
    img = img.get("url") if isinstance(img, dict) else (img or "")
    return {
        "title": re.sub(r"\s+", " ", str(p.get("headline") or "")).strip(),
        "url": url,
        "slug": slug,
        "category": category,
        "published": str(p.get("datePublished") or ""),
        "image": _norm_img(str(img or "")),
        "description": str(p.get("description") or ""),
    }


def parse_homepage(html: str) -> list[dict]:
    """Return items from the homepage feed (category left untagged)."""
    out: list[dict] = []
    seen: set[str] = set()
    for p in _extract_posts(html):
        it = _to_item(p, "")
        if it and it["slug"] not in seen:
            seen.add(it["slug"])
            out.append(it)
    return out


def parse_category(html: str, cat: str) -> list[dict]:
    """Return items from one category listing page, each tagged with `cat`."""
    out: list[dict] = []
    seen: set[str] = set()
    for p in _extract_posts(html):
        it = _to_item(p, cat)
        if it and it["slug"] not in seen:
            seen.add(it["slug"])
            out.append(it)
    return out


def parse_published(ts: str) -> dt.datetime | None:
    if not ts:
        return None
    try:
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


def ep_key(it: dict) -> str:
    """Stable per-episode dedup key: slug + episode spec from the headline.

    Every episode of a drama shares the series slug, so the slug alone would
    swallow later episodes. The episode spec keeps a batch range ("1 - 32")
    distinct from a later re-batch, so an updated batch notifies again.
    """
    slug = it.get("slug") or ""
    m = re.search(r"episode\s*0*(\d+(?:\s*-\s*\d+)?)", it.get("title", ""), re.IGNORECASE)
    ep = re.sub(r"\s+", "", m.group(1)) if m else "series"
    return f"{slug}#{ep}"


def parse_detail(html: str) -> dict:
    """Parse a drama detail page for its display title, synopsis and cover."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    synopsis = ""
    box = soup.find("div", id="synopsis")
    if box:
        p = box.find("p")
        if p:
            synopsis = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
            synopsis = re.sub(r"^Nodrakor Download Drama Korea [^-]+-\s*", "", synopsis)
    if not synopsis:
        meta = soup.find("meta", attrs={"property": "og:description"})
        if meta:
            synopsis = re.sub(r"\s+", " ", str(meta.get("content") or "")).strip()

    title = ""
    span = soup.find("span", class_=re.compile(r"border-b-4"))
    if span:
        title = re.sub(
            r"^Sinopsis\s+", "", span.get_text(" ", strip=True), flags=re.I
        ).replace(" Subtitle Indonesia", "").strip()
    if not title:
        t = soup.find("title")
        if t:
            title = (
                re.split(r"\s*-\s*Nodrakor", re.sub(r"\s+", " ", t.get_text()))[0]
                .strip()
                .replace(" Subtitle Indonesia", "")
            )

    cover = ""
    og = soup.find("meta", attrs={"property": "og:image"})
    if og:
        cover = _norm_img(str(og.get("content") or ""))
    return {"title": title, "synopsis": synopsis, "cover": cover}
