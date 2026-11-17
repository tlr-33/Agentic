"""Weekly run: dedupe -> sources (prune/explore + scrape) -> verify -> logos -> research -> Run Log.

    python run.py              # full run against Airtable
    python run.py --dry-run    # read Airtable, write nothing, log what would change
    python run.py --steps logos,research
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

import yaml

from sourcing import budget, manager, research
from sourcing.airtable import Airtable
from sourcing.dedupe import StartupIndex
from sourcing.llm import get_llm
from sourcing.scrapers import REGISTRY
from sourcing.scrapers.common import Context

STEPS = ["dedupe", "scrape", "verify", "logos", "research"]
log = logging.getLogger("run")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--steps", default=",".join(STEPS))
    args = ap.parse_args()
    steps = set(args.steps.split(","))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = yaml.safe_load(open("config.yaml"))
    at, today = Airtable(dry_run=args.dry_run), date.today()
    startups, sources = at.all("Startups"), at.all("Sources")
    mentions, runs = at.all("Mentions", ["Title", "URL", "Summary", "Startup"]), at.all("Run Log", ["Run"])
    options = at.select_options()
    room = {"left": cfg["airtable"]["max_records"] - len(startups) - len(sources) - len(mentions) - len(runs)}
    notes: list[str] = []
    totals = {"new": 0, "units": 0, "verified": 0, "rejected": 0}
    sources_run: list[str] = []

    if "dedupe" in steps:
        notes += manager.merge_duplicates(at, startups)
    index = StartupIndex(startups)

    if "scrape" in steps:
        runnable = set(REGISTRY)
        pending = {}
        for s in startups:
            if s["fields"].get("Manager Verdict") == "Pending":
                for sid in s["fields"].get("Discovered By") or []:
                    pending[sid] = pending.get(sid, 0) + 1
        for sid, fields, note in budget.status_changes(sources, cfg, runnable, pending):
            at.update("Sources", [{"id": sid, "fields": fields}])
            next(s for s in sources if s["id"] == sid)["fields"].update(fields)
            notes.append(note)
        alloc = budget.allocate(sources, cfg, runnable)
        ctx = Context(cfg=cfg, today=today, known_urls={m["fields"].get("URL") for m in mentions})
        for s in sources:
            units = alloc.get(s["id"], 0)
            if not units:
                continue
            f = s["fields"]
            try:
                found, used = REGISTRY[f["Source"]](f, units, ctx)
            except Exception as e:  # a broken source must not stop the others
                log.exception("source %s failed", f["Source"])
                notes.append(f"{f['Source']}: failed ({e.__class__.__name__}), see Actions log")
                found, used = [], 1
            res = manager.intake(at, index, s, found, room, keep_empty=f["Source"] in cfg.get("source_urls", {}))
            ctx.known_urls |= {m.url for m in found}
            startups += res["records"]  # so this run's manager step sees the new candidates
            upd = {"Runs": (f.get("Runs") or 0) + 1, "Cost Units Spent": (f.get("Cost Units Spent") or 0) + used,
                   "Last Run": today.isoformat(), "Budget Share": round(units / cfg["budget"]["units_per_run"], 2)}
            if f.get("Status") == "Trial":
                upd["Trial Runs Left"] = max(0, (f.get("Trial Runs Left") or 0) - 1)
            at.update("Sources", [{"id": s["id"], "fields": upd}])
            sources_run.append(s["id"])
            totals["new"] += res["new"]
            totals["units"] += used
            notes.append(f"{f['Source']}: {used}/{units} units, {res['mentions']} mentions, "
                         f"{res['new']} new candidates, {res['known']} already known")
        if room["left"] <= 0:
            notes.append("Record cap reached (config airtable.max_records): some candidates were not saved.")

    llm_manager = get_llm(cfg["manager"]["model"])
    if "verify" in steps:
        if llm_manager:
            mentions = at.all("Mentions", ["Title", "URL", "Summary", "Startup"])
            v = manager.verify(at, llm_manager, startups, mentions, options, cfg, index)
            totals["verified"], totals["rejected"] = v["verified"], v["rejected"]
            notes.append(f"Manager: {v['verified']} verified, {v['rejected']} rejected, "
                         f"{v['needs_evidence']} need evidence")
        else:
            notes.append("Manager verification skipped: no ANTHROPIC_API_KEY (candidates stay Pending).")

    if "logos" in steps:
        lg = research.refresh_logos(at, startups, cfg, today)
        notes.append(f"Logos: {lg['updated']} updated from company websites, {lg['not_found']} not found")

    if "research" in steps:
        llm_research = get_llm(cfg["research"]["model"])
        if llm_research:
            n = research.research(at, llm_research, startups, options, cfg, today, index)
            notes.append(f"Research: {n} profiles refreshed")
            totals["units"] += llm_research.searches
        else:
            notes.append("Research skipped: no ANTHROPIC_API_KEY.")
    if llm_manager:
        totals["units"] += llm_manager.searches

    at.create("Run Log", [{
        "Run": f"Run {len(runs) + 1} ({today.isoformat()}{', dry run' if args.dry_run else ''})",
        "Date": today.isoformat(), "Sources Run": sources_run, "Candidates Found": totals["new"],
        "Verified": totals["verified"], "Rejected": totals["rejected"], "Cost Units": totals["units"],
        "Manager Feedback": "\n".join(notes),
    }])
    print("\n".join(notes))


if __name__ == "__main__":
    main()
