"""online-fix.me scraper.

Homepage lists ``article.news`` entries; each carries the game link
(``a.big-link``), title (``h2.title``), cover (``img.lazyload`` data-src) and
an ISO date (``time[datetime]``). Plain requests works (no Cloudflare).
"""
from __future__ import annotations

from modules.media.common.http import simple_get

BASE = "https://online-fix.me"


def fetch_homepage() -> str:
    return simple_get(BASE + "/", timeout=25)


def _slug_from_url(url: str) -> str:
    if not url:
        return ""
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1].replace(".html", "")


def parse_homepage(html: str) -> list[dict]:
    """Return [{title, url, slug, cover, date}] from article.news entries."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen: set[str] = set()
    for art in soup.find_all("article", class_="news"):
        link_el = art.find("a", class_="big-link")
        url = (link_el.get("href") if link_el else "") or ""
        slug = _slug_from_url(url)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        h2 = art.find("h2", class_="title")
        title = h2.get_text(strip=True) if h2 else slug
        img = art.find("img", class_="lazyload")
        cover = (img.get("data-src") or img.get("src") or "") if img else ""
        time_el = art.find("time")
        date = (time_el.get("datetime") or "") if time_el else ""
        out.append({"title": title, "url": url, "slug": slug, "cover": cover, "date": date})
    return out


def parse_posted(raw: str):
    """Parse the ISO ``time[datetime]`` value; None if unparseable."""
    import datetime as dt
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
