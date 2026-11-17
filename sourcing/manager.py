"""Layer 2 (manager). Three jobs:
  merge_duplicates  one row per company, ever (plain code)
  intake            scraper output -> Mentions + Pending startups, merged into existing rows (plain code)
  verify            Pending -> verdict: real startup? UChicago-affiliated? (Claude, needs API key)
"""
from __future__ import annotations

import logging

from .dedupe import StartupIndex, domain_of, duplicate_groups
from .scrapers.common import Mention

log = logging.getLogger(__name__)
S = "Startups"
COMPUTED = {"First Seen", "Latest Mention", "Mention Count", "Research Due", "Flags", "Company Age (yrs)"}
MULTI = {"School", "UChicago Affiliation", "Polsky Programs", "Mentions"}
VERDICTS = {"Verified startup", "Not a startup", "Not UChicago-affiliated", "Duplicate", "Needs evidence"}


# -- merge ----------------------------------------------------------------------
def merge_duplicates(at, startups: list[dict]) -> list[str]:
    """Collapse rows that are the same company: keep the most complete row, fill its blanks
    and links from the others, then delete the others."""
    notes = []
    for group in duplicate_groups(startups):
        group.sort(key=lambda r: (r["fields"].get("Manager Verdict") != "Verified startup",
                                  -len(r["fields"]), r.get("createdTime", "")))
        keep, extra = group[0], group[1:]
        fields: dict = {}
        for other in extra:
            for k, v in other["fields"].items():
                if k in COMPUTED or not v:
                    continue
                mine = fields.get(k, keep["fields"].get(k))
                if k in MULTI:
                    union = list(dict.fromkeys((mine or []) + v))
                    if union != (keep["fields"].get(k) or []):
                        fields[k] = union
                elif not mine:
                    fields[k] = v
        fields = {k: _writable(k, v) for k, v in fields.items()}
        if fields:
            at.update(S, [{"id": keep["id"], "fields": fields}])
            keep["fields"].update(fields)
        at.delete(S, [r["id"] for r in extra])
        for r in extra:
            startups.remove(r)
        notes.append(f"Merged {len(extra)} duplicate(s) into {keep['fields'].get('Startup')}")
    return notes


def _writable(field: str, value):
    """Convert a value read from Airtable into the shape Airtable accepts on write."""
    if field == "Logo":
        return [{"url": a["url"], "filename": a.get("filename")} for a in value]
    if field == "Polsky Staff Assignee":
        return [{"id": u["id"]} for u in value]
    return value


# -- intake ---------------------------------------------------------------------
def intake(at, index: StartupIndex, source: dict, mentions: list[Mention], room: dict,
           keep_empty: bool = False) -> dict:
    """New candidates become Pending rows credited to this source; known companies just get the
    new mention linked (and blanks like Website filled). room['left'] = records we may still create."""
    sid = source["id"]
    placeholders, fills, recs_by_id, per_mention = [], {}, {}, []
    for m in mentions:
        ids: list = []
        for c in m.candidates:
            rec = index.find(c.name, c.domain)
            if rec is None:
                if room["left"] - len(placeholders) <= 1:  # keep one slot for the mention itself
                    continue
                rec = {"id": f"new:{len(placeholders)}",
                       "fields": {"Startup": c.name, "Manager Verdict": "Pending", "Discovered By": [sid]}}
                placeholders.append(rec)
                index.add(rec)
            if not rec["id"].startswith("new:"):
                recs_by_id[rec["id"]] = rec
            target = rec["fields"] if rec["id"].startswith("new:") else fills.setdefault(rec["id"], {})
            for field, value in (("Domain", c.domain), ("Website", c.website), ("Description", c.description)):
                if value and not rec["fields"].get(field) and not target.get(field):
                    target[field] = value
            if rec not in ids:
                ids.append(rec)
        per_mention.append(ids)

    created = at.create(S, [p["fields"] for p in placeholders])
    for p, c in zip(placeholders, created):
        p["id"] = c["id"]  # the index now points at the real record
    room["left"] -= len(created)
    if updates := [{"id": rid, "fields": f} for rid, f in fills.items() if f]:
        at.update(S, updates)
        for u in updates:
            recs_by_id[u["id"]]["fields"].update(u["fields"])

    rows = []
    for m, recs in zip(mentions, per_mention):
        if (recs or keep_empty) and room["left"] > 0:
            rows.append({"Title": m.title[:250], "URL": m.url, "Summary": m.summary,
                         "Source": [sid], "Startup": [r["id"] for r in recs], "Found By": "Feed collector",
                         **({"Published": m.published} if m.published else {})})
            room["left"] -= 1
    at.create("Mentions", rows)
    return {"new": len(created), "known": len({r["id"] for recs in per_mention for r in recs}) - len(created),
            "mentions": len(rows), "records": placeholders[:len(created)]}


# -- verify ---------------------------------------------------------------------
VERIFY_SYSTEM = """You are the manager agent for the University of Chicago Polsky Center's startup tracker.
You judge one candidate company at a time. Be strict: "Verified startup" requires a page you can link to
that states the UChicago tie. Reply with one JSON object and nothing else."""

VERIFY_PROMPT = """Candidate: {name}
Website: {website}
Where our scrapers found it:
{found}

Decide, using web search if needed:
1. Is it a real startup (a young company building a product)? Not a lab, research group, nonprofit
   program, investor, accelerator, consultancy, local small business or large corporation.
2. Is it UChicago-affiliated? At least one founder is a UChicago student, alum, faculty member or
   researcher (including Argonne, Fermilab and the Lab Schools), or it was built in a UChicago program.
   A company that only licenses UChicago IP, with no UChicago founder, is NOT affiliated.

JSON keys:
"verdict": one of {verdicts}
"reason": one or two sentences
"affiliation_evidence": URL of the page that states the UChicago tie, or null
"school": list, subset of {schools}
"affiliation": list, subset of {affiliations}
"website": the company's own website URL, or null"""


def verify(at, llm, startups: list[dict], mentions: list[dict], options: dict, cfg: dict,
           index: StartupIndex) -> dict:
    pending = [s for s in startups if s["fields"].get("Manager Verdict") == "Pending"]
    pending = pending[:cfg["manager"]["max_candidates_per_run"]]
    found_in: dict[str, list[str]] = {}
    for m in mentions:
        for sid in m["fields"].get("Startup") or []:
            found_in.setdefault(sid, []).append(
                f"- {m['fields'].get('Title')} ({m['fields'].get('URL')}): {(m['fields'].get('Summary') or '')[:300]}")
    opts = options.get(S, {})
    schools, affs = opts.get("School", set()), opts.get("UChicago Affiliation", set())
    updates, stats = [], {"verified": 0, "rejected": 0, "needs_evidence": 0}
    for s in pending:
        f = s["fields"]
        out = llm.ask_json(VERIFY_SYSTEM, VERIFY_PROMPT.format(
            name=f.get("Startup"), website=f.get("Website") or "unknown",
            found="\n".join(found_in.get(s["id"], [])[:5]) or "- (no mention text)",
            verdicts=sorted(VERDICTS - {"Duplicate"}), schools=sorted(schools), affiliations=sorted(affs)),
            web_searches=3)
        if not out or out.get("verdict") not in VERDICTS:
            continue
        verdict, evidence = out["verdict"], _url(out.get("affiliation_evidence"))
        if verdict == "Verified startup" and not evidence:
            verdict = "Needs evidence"  # evidence rule: no link, no verification
        fields = {"Manager Verdict": verdict, "Manager Reason": str(out.get("reason") or "")[:1000]}
        if evidence:
            fields["Affiliation Evidence"] = evidence
        if verdict == "Verified startup":
            fields["School"] = [x for x in out.get("school") or [] if x in schools]
            fields["UChicago Affiliation"] = [x for x in out.get("affiliation") or [] if x in affs]
        site = _url(out.get("website"))
        if site and not f.get("Website"):
            twin = index.find(None, domain_of(site))
            if twin and twin["id"] != s["id"]:
                fields.update({"Manager Verdict": "Duplicate",
                               "Manager Reason": f"Same website as {twin['fields'].get('Startup')}."})
            else:
                fields.update({"Website": site, "Domain": domain_of(site)})
        updates.append({"id": s["id"], "fields": fields})
        f.update(fields)
        v = fields["Manager Verdict"]
        stats["verified" if v == "Verified startup" else "needs_evidence" if v == "Needs evidence" else "rejected"] += 1
    at.update(S, updates)
    return stats


def _url(v) -> str | None:
    return v if isinstance(v, str) and v.startswith("http") else None
