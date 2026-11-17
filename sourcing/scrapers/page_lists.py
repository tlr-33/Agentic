"""Any source whose input is a fixed list of pages (demo days, cohorts, exhibitor lists).
The URLs live in config.yaml -> source_urls, keyed by the source's Airtable name."""
from __future__ import annotations

import logging

from .common import Context, Mention, polsky_post

log = logging.getLogger(__name__)


def run(source: dict, units: int, ctx: Context) -> tuple[list[Mention], int]:
    urls = [u for u in ctx.cfg.get("source_urls", {}).get(source["Source"], []) if u not in ctx.known_urls]
    mentions, used = [], 0
    for url in urls[:units]:
        used += 1
        try:
            mentions.append(polsky_post(url))
        except Exception as e:  # one bad page shouldn't stop the run
            log.warning("page list: %s failed: %s", url, e)
    return mentions, used
