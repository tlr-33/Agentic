"""Shared pieces for Layer 1 scrapers (plain code, no LLM)."""
from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ..dedupe import domain_of

UA = "Mozilla/5.0 (compatible; PolskySourcingBot/0.1; +https://github.com/tlr-33/Agentic)"


@dataclass
class Candidate:
    name: str
    domain: str | None = None
    website: str | None = None
    description: str | None = None


@dataclass
class Mention:
    title: str
    url: str
    published: str | None          # YYYY-MM-DD
    summary: str = ""
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Context:
    cfg: dict
    today: date
    known_urls: set[str]           # Mention URLs already in Airtable; never re-add


def get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30, **kw)
    r.raise_for_status()
    return r


def text_of(html: str, limit: int | None = None) -> str:
    t = re.sub(r"\s+", " ", BeautifulSoup(html or "", "lxml").get_text(" ")).strip()
    return t[:limit] if limit else t


# Links to these sites are news, social, events or UChicago itself, not a startup's homepage.
NOT_STARTUP_SITES = (
    "uchicago.edu", "chicagobooth.edu", "uchicagomedicine.org", "anl.gov", "fnal.gov",
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com", "youtu.be",
    "tiktok.com", "medium.com", "substack.com", "github.com", "airtable.com", "eventbrite.com", "lu.ma",
    "google.com", "forms.gle", "bit.ly", "apple.com", "spotify.com", "crunchbase.com", "pitchbook.com",
    "wikipedia.org", "bizjournals.com", "prnewswire.com", "businesswire.com", "globenewswire.com",
    "accessnewswire.com", "citybiz.co", "techcrunch.com", "forbes.com", "wsj.com", "nytimes.com",
    "reuters.com", "bloomberg.com", "chicagotribune.com", "suntimes.com", "cbsnews.com", "nbcchicago.com",
    "abc7chicago.com", "chicagoinno.com", "builtinchicago.org", "gotechchicago.com",
    "edu", "gov", "alchemistaccelerator.com", "dualityaccelerator.com", "mhubchicago.com", "1871.com",
)  # bare "edu"/"gov" block every university and government site
CALL_TO_ACTION = re.compile(
    r"^(read|learn|click|sign|register|watch|listen|apply|view|see|download|here|more|this|link|"
    r"website|full|rsvp|join|subscribe|follow|visit|explore|get|find|check)\b", re.I)


LEGAL_TAIL = re.compile(r"[,\s]+(inc|llc|ltd|corp|co|pvt ltd|private limited|pbc)\.?$", re.I)


def clean_name(name: str) -> str:
    """'Chameleon Semiconductor, Inc' -> 'Chameleon Semiconductor'."""
    return LEGAL_TAIL.sub("", re.sub(r"\s+", " ", name)).strip(" .,:;|-\u2013\u2014")


def _blocked(domain: str) -> bool:
    return any(domain == s or domain.endswith("." + s) for s in NOT_STARTUP_SITES)


def startup_links(content_html: str) -> list[Candidate]:
    """Polsky posts link each startup's name to its homepage, e.g. <a href="https://tovie.com/">Tovie</a>.
    Keep links whose text looks like a name and whose target is a site's root, not an article."""
    out: dict[str, Candidate] = {}
    for a in BeautifulSoup(content_html or "", "lxml").find_all("a", href=True):
        name = clean_name(a.get_text(" "))
        if not (2 <= len(name) <= 60) or CALL_TO_ACTION.match(name) or len(name.split()) > 6:
            continue
        href = htmllib.unescape(a["href"]).strip()
        domain = domain_of(href)
        if not domain or not href.startswith("http"):
            continue
        u = urlparse(href)
        if domain.endswith("linkedin.com") and u.path.startswith("/company/"):
            out.setdefault(name.lower(), Candidate(name=name))  # name only; research finds the site
            continue
        if _blocked(domain) or u.path.strip("/").count("/") >= 1 or len(u.path.strip("/")) > 25:
            continue
        out.setdefault(domain, Candidate(name=name, domain=domain, website=f"{u.scheme}://{u.netloc}"))
    return list(out.values())


def polsky_post(url: str) -> Mention:
    """Fetch one page as a Mention. Polsky posts go through the WordPress API for clean content."""
    m = re.match(r"https?://polsky\.uchicago\.edu/\d{4}/\d{2}/\d{2}/([^/?#]+)", url)
    if m:
        posts = get("https://polsky.uchicago.edu/wp-json/wp/v2/posts",
                    params={"slug": m.group(1), "_fields": "date,link,title,excerpt,content"}).json()
        if posts:
            p = posts[0]
            return Mention(title=text_of(p["title"]["rendered"]), url=url, published=p["date"][:10],
                           summary=text_of(p["excerpt"]["rendered"], 500),
                           candidates=startup_links(p["content"]["rendered"]))
    soup = BeautifulSoup(get(url).text, "lxml")
    body = soup.find("article") or soup.find("main") or soup.body or soup
    title = soup.title.get_text(strip=True) if soup.title else url
    return Mention(title=title[:200], url=url, published=None, summary=text_of(str(body), 500),
                   candidates=startup_links(str(body)))
