"""freetp.org scraper.

Homepage lists ``article.home-new-card`` entries; each carries the game link
(``a.home-new-card__media``), title (``h3.home-new-card__title > a``) and a
cover (``img`` src, relative). Game pages live under ``/games/`` (new) or
``/po-seti/`` (older) and hold the description in ``.story``. Plain requests
works (no Cloudflare).
"""
from __future__ import annotations

from modules.media.common.http import simple_get

BASE = "https://freetp.org"


def fetch_homepage() -> str:
    return simple_get(BASE + "/", timeout=25)


def fetch_detail(url: str) -> str:
    return simple_get(url, timeout=25)


def _abs(url: str) -> str:
    if not url:
        return ""
    if url.startswith("http"):
        return url
    return BASE + url if url.startswith("/") else BASE + "/" + url


def _slug_from_url(url: str) -> str:
    if not url:
        return ""
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1].replace(".html", "")


def parse_homepage(html: str) -> list[dict]:
    """Return [{title, url, slug, cover}] from article.home-new-card entries."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen: set[str] = set()
    for art in soup.find_all("article", class_="home-new-card"):
        media = art.find("a", class_="home-new-card__media")
        url = (media.get("href") if media else "") or ""
        slug = _slug_from_url(url)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        h3 = art.find("h3", class_="home-new-card__title")
        title_el = h3.find("a") if h3 else None
        title = (title_el.get_text(strip=True) if title_el else slug)
        img = art.find("img")
        cover = _abs(img.get("src", "") if img else "")
        out.append({"title": title, "url": url, "slug": slug, "cover": cover})
    return out


def parse_detail(html: str) -> dict:
    """Return {description, downloads} from the game page."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one(".story") or soup.select_one(".full-story")
    desc = el.get_text(" ", strip=True) if el else ""
    return {"description": desc, "downloads": _extract_downloads(soup)}


def _extract_downloads(soup) -> list[dict]:
    import re
    out: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/getfile-" in href or ".torrent" in href.lower():
            label = re.sub(r"^\s*скачать\s+", "", a.get_text(strip=True), flags=re.I).strip() or "Download"
            url = href if href.startswith("http") else ("https:" + href if href.startswith("//") else BASE + href)
            out.append({"label": label, "url": url})
    return out
