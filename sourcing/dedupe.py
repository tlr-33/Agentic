"""One row per company. Match on website domain first, then on a normalized name."""
from __future__ import annotations

import html
import re
from urllib.parse import urlparse

LEGAL_SUFFIXES = {"inc", "llc", "ltd", "co", "corp", "corporation", "company", "pbc", "plc", "gmbh", "lp"}


def domain_of(url: str | None) -> str | None:
    """'https://www.Acme.com/about' -> 'acme.com'."""
    if not url:
        return None
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.") or None


def name_key(name: str | None) -> str:
    """'Clarix Imaging Corporation' -> 'clarix imaging'."""
    s = html.unescape(name or "").lower().replace("&", " and ")
    s = re.sub(r"[\u2019'`]", "", s)
    return " ".join(t for t in re.sub(r"[^a-z0-9]+", " ", s).split() if t not in LEGAL_SUFFIXES)


def _domain(rec: dict) -> str | None:
    f = rec["fields"]
    return f.get("Domain") or domain_of(f.get("Website"))


class StartupIndex:
    """In-memory view of the Startups table used to merge new candidates into existing rows."""

    def __init__(self, records=()):
        self.by_domain: dict[str, dict] = {}
        self.by_name: dict[str, dict] = {}
        for r in records:
            self.add(r)

    def add(self, rec: dict) -> None:
        if d := _domain(rec):
            self.by_domain.setdefault(d, rec)
        if k := name_key(rec["fields"].get("Startup")):
            self.by_name.setdefault(k, rec)

    def find(self, name: str | None, domain: str | None = None) -> dict | None:
        if domain and domain in self.by_domain:
            return self.by_domain[domain]
        key = name_key(name)
        if not key:
            return None
        rec = self.by_name.get(key)
        if rec is None:  # "Clarix Imaging" ~ "Clarix Imaging Systems", only for 2+ word names
            for k, r in self.by_name.items():
                short, long_ = sorted((k, key), key=len)
                if len(short.split()) >= 2 and long_.startswith(short + " "):
                    rec = r
                    break
        if rec and domain and _domain(rec) not in (None, domain):
            return None  # same name but a different website: treat as a different company
        return rec


def duplicate_groups(records: list[dict]) -> list[list[dict]]:
    """Groups of rows that are certainly the same company (same domain or same exact name)."""
    parent = list(range(len(records)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[tuple, int] = {}
    for i, r in enumerate(records):
        for key in (("d", _domain(r)), ("n", name_key(r["fields"].get("Startup")))):
            if not key[1]:
                continue
            if key in seen:
                j = seen[key]
                if key[0] == "n" and None not in (_domain(r), _domain(records[j])) and _domain(r) != _domain(records[j]):
                    continue  # same name, different websites: two companies
                parent[root(i)] = root(j)
            else:
                seen[key] = i
    groups: dict[int, list[dict]] = {}
    for i, r in enumerate(records):
        groups.setdefault(root(i), []).append(r)
    return [g for g in groups.values() if len(g) > 1]
