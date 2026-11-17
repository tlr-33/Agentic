"""Polsky company directory (WordPress 'company' type). First run backfills everything;
later runs only fetch entries modified since Last Run."""
from __future__ import annotations

from .common import Candidate, Context, Mention, clean_name, get, text_of

API = "https://polsky.uchicago.edu/wp-json/wp/v2/company"


def run(source: dict, units: int, ctx: Context) -> tuple[list[Mention], int]:
    skip = [s.lower() for s in ctx.cfg.get("directory_skip", [])]
    params = {"per_page": 100, "_fields": "slug,link,date,title,content"}
    if source.get("Last Run"):
        params["modified_after"] = f"{source['Last Run']}T00:00:00"
    mentions, used, page = [], 0, 1
    while used < units:
        r = get(API, params={**params, "page": page})
        used += 1
        for c in r.json():
            name = clean_name(text_of(c["title"]["rendered"]))
            desc = text_of(c["content"]["rendered"], 1000)
            if (c["slug"].endswith("-2") or c["link"] in ctx.known_urls
                    or "lorem ipsum" in desc.lower() or any(s in name.lower() for s in skip)):
                continue  # duplicate slug, already seen, placeholder text, or not a startup
            mentions.append(Mention(title=f"Polsky directory: {name}", url=c["link"],
                                    published=c["date"][:10], summary=desc[:500],
                                    candidates=[Candidate(name=name, description=desc)]))
        if page >= int(r.headers.get("X-WP-TotalPages") or 0):
            break
        page += 1
    return mentions, used
