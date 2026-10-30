"""Game3rb — PC game notifier for https://game3rb.com/category/games-online/.

Parses the category page for game posts, fetches each post for
synopsis, cover image, and download link.
"""
from __future__ import annotations

import datetime as dt
import re

from modules.media.common.http import simple_get

BASE = "https://game3rb.com"
CATEGORY_URL = BASE + "/category/games-online/"


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fetch_category_page(page: int = 1) -> str:
    url = f"{CATEGORY_URL}page/{page}/" if page > 1 else CATEGORY_URL
    return simple_get(url, timeout=25, headers={"User-Agent": UA})


def parse_category_page(html: str) -> list[dict]:
    """Return [{title, url, slug]} from game posts on category page."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen = set()

    container = soup.find("div", class_="main-posts") or soup
    articles = container.find_all("article", class_="post-hentry")

    for article in articles:
        h3 = article.find("h3", class_="entry-title")
        if not h3:
            continue
        a = h3.find("a", href=True)
        if not a:
            continue
        href = str(a["href"])
        if not href.startswith(f"{BASE}/"):
            continue
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if not slug or len(slug) < 5:
            continue
        if any(x in slug for x in (
            "contact-us", "dmca", "donate", "deleted-games",
            "how-to-download", "read-before-download",
        )):
            continue
        if slug in seen:
            continue
        seen.add(slug)
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 3:
            continue
        out.append({"title": title, "url": href, "slug": slug})
    return out


def fetch_detail(url: str) -> str:
    return simple_get(url, timeout=25, headers={"User-Agent": UA})


def parse_detail(html: str) -> dict:
    """Parse a game post for synopsis, cover, and download link."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    # Title (full, no cleaning)
    title = ""
    og = soup.find("meta", property="og:title")
    if og:
        title = str(og.get("content", ""))
    if not title:
        h1 = soup.find("h1", class_="post-title") or soup.find("h1")
        if h1:
            title = h1.get_text(" ", strip=True)

    # Cover image
    cover = ""
    og_img = soup.find("meta", property="og:image")
    if og_img:
        cover = og_img.get("content", "")
    if not cover:
        # fallback: first content image
        for img in soup.find_all("img", src=True):
            src = str(img["src"])
            if "wp-content/uploads" in src and "cropped" not in src:
                cover = src
                break

    # Synopsis / description - extract from the summary section
    synopsis = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        synopsis = og_desc.get("content", "")
    if not synopsis:
        # Game description snippet (the actual game blurb on the page)
        snippet = soup.find("div", class_="game_description_snippet")
        if snippet:
            synopsis = re.sub(r"\s+", " ", snippet.get_text(" ", strip=True)).strip()
    if not synopsis:
        # Try to find the summary text from the post content
        summary_div = soup.find("div", class_="summaryy")
        if summary_div:
            # Extract only the Game Details section
            text = summary_div.get_text(" ", strip=True)
            # Try to find RELEASE NAME, DEVELOPER, GENRE, etc.
            details = {}
            for field in [
                "RELEASE NAME", "RELEASE SIZE", "DEVELOPER", "PUBLISHER",
                "RELEASE DATE", "GENRE", "ALL REVIEWS",
            ]:
                m = re.search(rf"{field}:\s*([^\n]+)", text)
                if m:
                    details[field] = m.group(1).strip()
            if details:
                synopsis = " | ".join(f"{k}: {v}" for k, v in details.items())
    if not synopsis:
        post_body = soup.find("div", class_="entry-content") or soup.find(
            "div", class_="post-content"
        )
        if post_body:
            p = post_body.find("p")
            if p:
                synopsis = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()

    # Download link: thenewscasts.com
    download = ""
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if "thenewscasts.com" in href:
            download = href
            break

    # Published date
    posted = ""
    m = re.search(r'"datePublished"[^>]*content="([^"]+)"', html)
    if m:
        posted = m.group(1)
    else:
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
            return dt.datetime.strptime(ts, fmt).replace(tzinfo=dt.UTC)
        except Exception:
            continue
    return None


if __name__ == "__main__":
    items = parse_category_page(fetch_category_page())
    print(f"parsed {len(items)} items")
    for it in items[:5]:
        print(" ", it["title"][:60], "|", it["url"])
