"""Polsky news feed: new posts from the Polsky WordPress API since this source's Last Run."""
from __future__ import annotations

import re
from datetime import date, timedelta

from .common import Context, Mention, get, startup_links, text_of

API = "https://polsky.uchicago.edu/wp-json/wp/v2/posts"
SKIP_CATEGORIES = "592,3712"  # Podcast, Thought Leadership (no startups)
SKIP_TITLES = re.compile(r"small business spotlight", re.I)  # local small businesses, not startups


def run(source: dict, units: int, ctx: Context) -> tuple[list[Mention], int]:
    last = source.get("Last Run")
    since = date.fromisoformat(last) - timedelta(days=1) if last else ctx.today - timedelta(days=30)
    mentions, used, page = [], 0, 1
    while used < units:
        r = get(API, params={"after": f"{since}T00:00:00", "per_page": 20, "page": page,
                             "categories_exclude": SKIP_CATEGORIES, "orderby": "date", "order": "asc",
                             "_fields": "date,link,title,excerpt,content"})
        used += 1
        for p in r.json():
            title = text_of(p["title"]["rendered"])
            if SKIP_TITLES.search(title) or p["link"] in ctx.known_urls:
                continue
            mentions.append(Mention(title=title, url=p["link"], published=p["date"][:10],
                                    summary=text_of(p["excerpt"]["rendered"], 500),
                                    candidates=startup_links(p["content"]["rendered"])))
        if page >= int(r.headers.get("X-WP-TotalPages") or 0):
            break
        page += 1
    return mentions, used
