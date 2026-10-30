"""OvaGames PC-game notifier.

Scrapes www.ovagames.com homepage (h2 + link list), then the detail
page of each new post for synopsis + download mirrors (filecrypt.cc).

Homepage and post pages are NOT Cloudflare-blocked (only /feed/ and
/wp-json/ are), so plain requests works.
"""
from __future__ import annotations

import os
import re
import datetime as dt

from modules.media.common.http import simple_get

BASE = "https://www.ovagames.com"

# Mirror hosts on post pages: filecrypt.cc containers (mirror shortener).
MIRROR_HOSTS = (
    "filecrypt.cc", "1fichier.com", "uptobox.com", "zippyshare.com",
    "multiup.org", "dailyuploads.net", "clicknupload.co", "sendspace.com",
    "mega.nz", "mediafire.com", "uploadhaven.com", "datanodes.to",
    "filesfm.com", "gofile.io", "katfile.com", "rapidgator.net",
    "turbobit.net", "nitroflare.com", "k2s.cc", "hxfile.co",
    "racaty.net", "userscloud.com", "uploadbuzz.cc", "filer.net",
)


def _slug_from_url(url: str) -> str:
    if not url:
        return ""
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def _clean_title(raw: str) -> str:
    """'A 1800 Complete Edition-Repack &#8211; Skidrow ...' -> game name."""
    t = raw or ""
    t = re.sub(r"&#8211;|&#8212;", "-", t)
    t = re.sub(r"\s*[-\u2013]\s*(Skidrow\s*&amp;?\s*Reloaded.*|Skidrow.*)$", "", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or raw


def fetch_homepage() -> str:
    return simple_get(BASE + "/", timeout=25)


def parse_homepage(html: str) -> list[dict]:
    """Return [{title, url, slug}] from homepage h2 > a entries."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen = set()
    for h in soup.find_all(["h1", "h2", "h3"]):
        a = h.find("a", href=True)
        if not a:
            continue
        url = str(a["href"])
        if not url.startswith(BASE):
            continue
        slug = _slug_from_url(url)
        if slug in seen:
            continue
        seen.add(slug)
        title = _clean_title(a.get_text(" ", strip=True)) or _clean_title(h.get_text(" ", strip=True))
        out.append({"title": title, "url": url, "slug": slug})
    return out


def fetch_detail(url: str) -> str:
    return simple_get(url, timeout=25)


def parse_detail(html: str) -> dict:
    """Parse a post page: synopsis (ABOUT THE GAME), download mirrors, cover, date."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    synopsis = ""
    # OvaGames uses .wp-tab-content-wrapper blocks (not wordpress-post-tabs):
    # first wrapper = game description, third = system requirements.
    wrappers = soup.select(".wp-tab-content-wrapper")
    if wrappers:
        synopsis = re.sub(r"\s+", " ", wrappers[0].get_text(" ", strip=True)).strip()
    if not synopsis:
        tabs = soup.select_one(".wordpress-post-tabs")
        if tabs:
            tab0 = tabs.select_one("div[id$='-0-0'], div[id*='-0-0']")
            if tab0:
                about = tab0.find("h5")
                if about and re.match(r"(?i)about the game", about.get_text(strip=True)):
                    p = about.find_next("p")
                    if p:
                        synopsis = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()

    # download mirrors: links inside .dl-wraps-item (the download section).
    # OvaGames routes through shorteners now (srnky/clksz/shrinkme) with the
    # mirror name as the anchor label, so match the download block + label,
    # not the href host. Fall back to the known-mirror-host match for the
    # older filecrypt-style layout.
    mirrors: list[dict] = []
    seen_u = set()
    dl_blocks = soup.select(".dl-wraps-item")
    if dl_blocks:
        for block in dl_blocks:
            for a in block.find_all("a", href=True):
                href = str(a["href"])
                if not href.startswith("http") or href in seen_u:
                    continue
                seen_u.add(href)
                label = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
                label = re.sub(r"^[•\-\s]+", "", label).strip()
                if not label or label.lower() in ("download", "here", "link"):
                    label = "Mirror"
                mirrors.append({"label": label, "url": href})
                if len(mirrors) >= 15:
                    break
            if len(mirrors) >= 15:
                break
    else:
        for a in soup.find_all("a", href=True):
            href = str(a["href"])
            host = re.sub(r"^https?://", "", href).split("/")[0].lower()
            host = re.sub(r"^www\d*\.", "", host)
            if host not in MIRROR_HOSTS or href in seen_u:
                continue
            seen_u.add(href)
            label = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
            if not label or label.lower() in ("download", "here", "link"):
                label = re.sub(r"^www\d*\.", "", host)
            mirrors.append({"label": label, "url": href})
            if len(mirrors) >= 15:
                break

    cover = ""
    m = re.search(r'<meta property="og:image"[^>]+content="([^"]+)"', html)
    if m:
        cover = m.group(1)
    if not cover:
        # fallback: first wp-content/uploads image that isn't a site logo/background
        for im in soup.find_all("img", src=True):
            src = str(im["src"])
            if "wp-content/uploads" not in src:
                continue
            if re.search(r"(logo|backgroundpic|icon)", src, re.I):
                continue
            cover = src
            break

    posted = ""
    m = re.search(r"(?:Posted\s+)?(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}", html)
    if m:
        posted = m.group(0).replace("Posted ", "")

    return {"synopsis": synopsis[:600], "mirrors": mirrors, "cover": cover, "posted": posted}


def parse_posted(ts: str):
    if not ts:
        return None
    try:
        d = dt.datetime.strptime(ts, "%B %d, %Y").replace(tzinfo=dt.timezone.utc)
        return d
    except Exception:
        return None


if __name__ == "__main__":
    items = parse_homepage(fetch_homepage())
    print(f"parsed {len(items)} items")
    for it in items[:5]:
        print(" ", it["title"][:60], "|", it["url"])
