"""ZeiGames — PC game notifier for https://zeigames.com/tags/online/.

Parses the tag page for game topics, fetches each topic page for
synopsis, cover image, and download link (zeilink.net).
"""
from __future__ import annotations

import re
import datetime as dt

from modules.media.common.http import cf_get

BASE = "https://zeigames.com"
TAG_URL = BASE + "/tags/online/"


def fetch_tag_page(page: int = 1) -> str:
    url = f"{TAG_URL}?page={page}" if page > 1 else TAG_URL
    return cf_get(url, timeout=25)


def parse_tag_page(html: str) -> list[dict]:
    """Return [{title, url, slug}] from topic list on the tag page."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if not href.startswith(f"{BASE}/topic/"):
            continue
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        # filter: only actual game posts (contain "free-download")
        if "free-download" not in slug:
            continue
        if slug in seen:
            continue
        seen.add(slug)
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        out.append({"title": title, "url": href, "slug": slug})
    return out


def fetch_detail(url: str) -> str:
    return cf_get(url, timeout=25)


def parse_detail(html: str) -> dict:
    """Parse a game topic page for synopsis, cover, and download link."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = ""
    og = soup.find("meta", property="og:title")
    if og:
        title = og.get("content", "")

    # Cover image
    cover = ""
    og_img = soup.find("meta", property="og:image")
    if og_img:
        cover = og_img.get("content", "")

    # Synopsis / description
    synopsis = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        synopsis = og_desc.get("content", "")
    if not synopsis:
        # fallback: first <p> in the post body
        post_body = soup.select_one(".ipsType_normal, .cPost_body")
        if post_body:
            p = post_body.find("p")
            if p:
                synopsis = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()

    # Download link: zeilink.net or thenewscasts.com short link
    download = ""
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if "zeilink.net" in href or "thenewscasts.com" in href:
            download = href
            break

    # Published date
    posted = ""
    m = re.search(r'"datePublished"[^>]*content="([^"]+)"', html)
    if m:
        posted = m.group(1)
    else:
        # fallback: look for time element
        time_el = soup.find("time")
        if time_el and time_el.get("datetime"):
            posted = time_el["datetime"]

    return {
        "title": title[:300],
        "synopsis": synopsis[:600],
        "cover": cover,
        "download": download,
        "posted": posted,
    }


def parse_posted(ts: str):
    if not ts:
        return None
    try:
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d
    except Exception:
        pass
    for fmt in ("%B %d, %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(ts, fmt).replace(tzinfo=dt.timezone.utc)
        except Exception:
            continue
    return None


if __name__ == "__main__":
    items = parse_tag_page(fetch_tag_page())
    print(f"parsed {len(items)} items")
    for it in items[:5]:
        print(" ", it["title"][:60], "|", it["url"])
