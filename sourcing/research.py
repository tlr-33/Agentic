"""Layer 3 (research), verified startups only.
  refresh_logos  logo from the startup's own website (plain code, no API key)
  research       profile fields with source links, only for rows Airtable marks "Research Due" (Claude)
"""
from __future__ import annotations

import logging
from datetime import date

from .dedupe import StartupIndex, domain_of
from .logos import find_logo
from .manager import _url

log = logging.getLogger(__name__)
S = "Startups"


def _verified(s: dict) -> bool:
    return s["fields"].get("Manager Verdict") == "Verified startup"


def refresh_logos(at, startups: list[dict], cfg: dict, today: date) -> dict:
    """'Logo Source' records what we did per site: '<domain> · <method> · <image url>' or
    '<domain> · not found · <date>'. A site is re-checked when its Website changes, and
    weekly if nothing was found."""
    notes, stats = [], {"updated": 0, "not_found": 0}
    for s in startups:
        f = s["fields"]
        site = domain_of(f.get("Website"))
        if not (_verified(s) and site):
            continue
        done = (f.get("Logo Source") or "").split(" · ")
        if done[0] == site and (done[1:2] != ["not found"] or _days_since(done[-1], today) < 7):
            continue
        if stats["updated"] + stats["not_found"] >= cfg["logos"]["max_per_run"]:
            break
        logo = find_logo(f["Website"])
        if logo:
            at.replace_attachment(S, s, "Logo", logo["png"], f"{site}.png")
            note = f"{site} · {logo['method']} · {logo['source'][:300]}"
            stats["updated"] += 1
        else:  # keep whatever logo is there; never blank a card
            note = f"{site} · not found · {today}"
            stats["not_found"] += 1
        notes.append({"id": s["id"], "fields": {"Logo Source": note}})
        f["Logo Source"] = note
    at.update(S, notes)
    return stats


def _days_since(iso: str, today: date) -> int:
    try:
        return (today - date.fromisoformat(iso)).days
    except ValueError:
        return 999


RESEARCH_SYSTEM = """You are the research agent for the University of Chicago Polsky Center's startup tracker.
Fill one company profile from public sources. Every fact must come from a page you opened; if no source
states a fact, use null. Never guess personal emails. Reply with one JSON object and nothing else."""

RESEARCH_PROMPT = """Company: {name}
Website: {website}
What we already have (confirm or correct): {current}

JSON keys:
"website": the company's own site, or null
"description": one or two plain sentences on what they do
"industry": short label, e.g. "Medical devices"
"hq": "City, State" or "City, Country"
"founders": "Name (role); Name (role)"
"founded_year": integer, "founded_evidence": URL stating it
"funding_round": latest round, one of {rounds}
"total_raised_usd": number, "last_funding_date": "YYYY-MM-DD", "funding_evidence": URL stating it
"company_status": one of {statuses}
"email": a general contact address printed on the company's own site, or null"""


def research(at, llm, startups: list[dict], options: dict, cfg: dict, today: date,
             index: StartupIndex) -> int:
    due = [s for s in startups if _verified(s) and s["fields"].get("Research Due") == "Due"]
    opts = options.get(S, {})
    rounds, statuses = opts.get("Funding Round", set()), opts.get("Company Status", set())
    updates = []
    for s in due[:cfg["research"]["max_profiles_per_run"]]:
        f = s["fields"]
        current = {k: f.get(k) for k in ("Description", "Industry", "HQ", "Founder(s)", "Founded Year",
                                         "Funding Round", "Total Raised", "Last Funding Date", "Company Status")}
        out = llm.ask_json(RESEARCH_SYSTEM, RESEARCH_PROMPT.format(
            name=f.get("Startup"), website=f.get("Website") or "unknown",
            current={k: v for k, v in current.items() if v}, rounds=sorted(rounds), statuses=sorted(statuses)),
            web_searches=5)
        if not out:
            continue
        new: dict = {"Last Researched": today.isoformat()}

        def fill(field, value):  # only fills blanks; never overwrites a human's entry
            if value and not f.get(field):
                new[field] = value

        site = _url(out.get("website"))
        if site and not f.get("Website"):
            twin = index.find(None, domain_of(site))
            if not twin or twin["id"] == s["id"]:
                new.update({"Website": site, "Domain": domain_of(site)})
        fill("Description", out.get("description"))
        fill("Industry", out.get("industry"))
        fill("HQ", out.get("hq"))
        fill("Founder(s)", out.get("founders"))
        if _url(out.get("founded_evidence")) and isinstance(out.get("founded_year"), int):
            fill("Founded Year", out["founded_year"])
            fill("Founded Evidence", out["founded_evidence"])
        if _url(out.get("funding_evidence")) and (out.get("last_funding_date") or "") > (f.get("Last Funding Date") or ""):
            new.update({"Funding Evidence": out["funding_evidence"], "Last Funding Date": out["last_funding_date"]})
            if isinstance(out.get("total_raised_usd"), (int, float)):
                new["Total Raised"] = out["total_raised_usd"]
            if out.get("funding_round") in rounds:
                new["Funding Round"] = out["funding_round"]
        if out.get("company_status") in statuses:
            new["Company Status"] = out["company_status"]
        email = out.get("email") or ""
        if "@" in email and email.split("@")[-1].lower() == domain_of(f.get("Website") or site or ""):
            fill("Email", email)
        updates.append({"id": s["id"], "fields": new})
        f.update(new)
    at.update(S, updates)
    return len(updates)
