"""Logos pulled from each startup's own website, so the card shows the mark the company itself uses.

Order of preference (first one that loads and is big enough wins):
  1. Organization logo declared in the site's structured data (JSON-LD)
  2. <img> or inline <svg> marked as the logo in the site header/nav
  3. apple-touch-icon / large declared icons
Plain favicons are never used: they were the blurry 32-64px images in the first run.
"""
from __future__ import annotations

import io
import json
import logging
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image

from .dedupe import domain_of

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
MIN_SIDE = 96     # longest side in px; anything smaller looks blurry on a gallery card
MAX_SIDE = 800
LOGO_HINT = re.compile(r"logo|brand|site-title|navbar-brand|wordmark", re.I)
NOT_OURS = re.compile(r"partner|client|sponsor|investor|backed|featured|press|award|badge|customer|"
                      r"trusted|as-seen|seal|certif|footer-logo|payment", re.I)


def find_logo(website: str) -> dict | None:
    """Best logo on the site -> {'png': bytes, 'source': url, 'method': str}, or None."""
    try:
        r = requests.get(website, headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        log.info("logo: %s unreachable (%s)", website, e)
        return None
    for _, method, url, inline_svg in _candidates(r.text, r.url):
        img = _load(url, inline_svg)
        if img is None or max(img.size) < MIN_SIDE:
            continue
        return {"png": _to_png(img), "source": url or f"{r.url}#inline-svg", "method": method}
    return None


def _candidates(html: str, base: str) -> list[tuple[int, str, str | None, str | None]]:
    soup = BeautifulSoup(html, "lxml")
    brand = (domain_of(base) or "").split(".")[0]
    found: list[tuple[int, str, str | None, str | None]] = []

    for tag in soup.find_all("script", type="application/ld+json"):  # 1. structured data
        try:
            for logo in _walk_logo(json.loads(tag.string or "")):
                found.append((100, "json-ld", urljoin(base, logo), None))
        except (ValueError, TypeError):
            pass

    header = soup.find(["header", "nav"]) or soup.find(class_=re.compile(r"header|navbar|nav", re.I))
    for scope, bonus in ((header, 10), (soup, 0)):  # 2. marked logo, header first
        if scope is None:
            continue
        for img in scope.find_all("img")[:30]:
            src = img.get("src") or img.get("data-src") or _first_srcset(img.get("srcset"))
            attrs = " ".join([src or "", img.get("alt") or "", img.get("id") or "", *(img.get("class") or []),
                              *(" ".join(p.get("class") or []) + " " + (p.get("id") or "")
                                for p in list(img.parents)[:3])])
            if not src or src.startswith("data:") or NOT_OURS.search(attrs):
                continue
            home = img.find_parent("a", href=True)
            in_home_link = bool(home) and urljoin(base, home["href"]).rstrip("/") == base.rstrip("/").split("?")[0]
            if LOGO_HINT.search(attrs) or in_home_link or (bonus and brand and brand in attrs.lower()):
                found.append((80 + bonus, "header-img" if bonus else "page-img", urljoin(base, src), None))
        for svg in scope.find_all("svg")[:15]:
            attrs = " ".join([" ".join(svg.get("class") or []), svg.get("aria-label") or "",
                              " ".join(" ".join(p.get("class") or []) for p in list(svg.parents)[:3])])
            if LOGO_HINT.search(attrs) and not NOT_OURS.search(attrs):
                found.append((75 + bonus, "inline-svg", None, str(svg)))

    for link in soup.find_all("link", href=True):  # 3. large declared icons
        rel = " ".join(link.get("rel") or []).lower()
        sizes = max((int(n) for n in re.findall(r"(\d+)x\d+", link.get("sizes") or "")), default=0)
        if "apple-touch-icon" in rel:
            found.append((60, "apple-touch-icon", urljoin(base, link["href"]), None))
        elif "icon" in rel and (sizes >= MIN_SIDE or link["href"].lower().endswith(".svg")):
            found.append((55, "icon", urljoin(base, link["href"]), None))
    og = soup.find("meta", property="og:image")
    if og and og.get("content") and "logo" in og["content"].lower():
        found.append((50, "og-logo", urljoin(base, og["content"]), None))

    seen, out = set(), []
    for c in sorted(found, key=lambda c: -c[0]):
        key = c[2] or c[3]
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _walk_logo(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk_logo(n)
    elif isinstance(node, dict):
        logo = node.get("logo")
        if isinstance(logo, str):
            yield logo
        elif isinstance(logo, dict) and isinstance(logo.get("url"), str):
            yield logo["url"]
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _walk_logo(v)


def _first_srcset(srcset: str | None) -> str | None:
    return srcset.split(",")[-1].strip().split(" ")[0] if srcset else None  # last = largest


def _load(url: str | None, inline_svg: str | None) -> Image.Image | None:
    try:
        if inline_svg:
            data, is_svg = inline_svg.encode(), True
            if b"xmlns=" not in data[:300]:
                data = data.replace(b"<svg", b'<svg xmlns="http://www.w3.org/2000/svg"', 1)
        else:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            r.raise_for_status()
            data = r.content
            is_svg = "svg" in r.headers.get("content-type", "") or url.lower().split("?")[0].endswith(".svg")
        if is_svg:
            import cairosvg  # imported lazily: needs the system cairo library
            data = cairosvg.svg2png(bytestring=data, output_width=512)
        img = Image.open(io.BytesIO(data))
        if getattr(img, "format", "") == "ICO":
            img.size = max(img.ico.sizes())
        img.load()
        img = img.convert("RGBA")
        return img if img.getchannel("A").getbbox() else None  # reject fully transparent renders
    except Exception as e:
        log.debug("logo candidate %s failed: %s", url or "inline svg", e)
        return None


def _to_png(img: Image.Image) -> bytes:
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] < 255:  # has transparency: a white logo would vanish on a white card
        small = img.copy()
        small.thumbnail((64, 64))
        visible = [p for p in small.getdata() if p[3] > 128]
        if visible and sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b, _ in visible) / len(visible) > 225:
            bg = Image.new("RGBA", img.size, (31, 41, 55, 255))
            bg.alpha_composite(img)
            img = bg
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
