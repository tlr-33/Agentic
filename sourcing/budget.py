"""Where scraper effort goes. Two rules, both tuned in config.yaml -> budget.

Prune (LASSO-style): a source whose yield stays low is set to Pruned and gets zero budget.
Explore (bandit-style): a fixed slice of every run goes to Trial sources, so new sources get tried.

Yield = new verified startups per 10 units, computed by the "Yield (per 10 units)" formula in Airtable.
"""
from __future__ import annotations


def _yield(src: dict) -> float:
    return float(src["fields"].get("Yield (per 10 units)") or 0)


def status_changes(sources: list[dict], cfg: dict, runnable: set[str],
                   pending_by_source: dict[str, int]) -> list[tuple[str, dict, str]]:
    """Promote / prune decisions made at the start of a run. Returns [(record_id, fields, note)]."""
    b = cfg["budget"]
    out = []
    for s in sources:
        f = s["fields"]
        name, status = f.get("Source"), f.get("Status")
        if pending_by_source.get(s["id"]):
            continue  # don't judge a source while its candidates are still unjudged
        good = _yield(s) >= b["prune_if_yield_below"]
        if status == "Proposed" and name in runnable:
            out.append((s["id"], {"Status": "Trial", "Trial Runs Left": b["trial_runs"]},
                        f"{name}: Proposed -> Trial ({b['trial_runs']} runs)"))
        elif status == "Trial" and (f.get("Trial Runs Left") or 0) <= 0:
            new = "Active" if good else "Pruned"
            out.append((s["id"], {"Status": new, **({} if good else {"Budget Share": 0})},
                        f"{name}: trial finished, yield {_yield(s):g} -> {new}"))
        elif status == "Active" and (f.get("Runs") or 0) >= b["prune_after_runs"] and not good:
            out.append((s["id"], {"Status": "Pruned", "Budget Share": 0},
                        f"{name}: yield {_yield(s):g} after {f.get('Runs')} runs -> Pruned"))
    return out


def allocate(sources: list[dict], cfg: dict, runnable: set[str]) -> dict[str, int]:
    """Units per source for this run: {record_id: units}. Units are a cap, not a target."""
    b, pins = cfg["budget"], cfg.get("pin_shares") or {}
    total = b["units_per_run"]
    by_status = lambda st: [s for s in sources
                            if s["fields"].get("Status") == st and s["fields"].get("Source") in runnable]
    active, trial = by_status("Active"), by_status("Trial")

    units: dict[str, int] = {}
    if trial:  # explore slice, split evenly across trials
        each = max(1, round(total * b["explore_share"]) // len(trial))
        for s in trial:
            units[s["id"]] = min(b["max_units_per_trial"], each)

    pool = total - sum(units.values())
    pinned = {s["id"]: pins[s["fields"]["Source"]] for s in active if s["fields"]["Source"] in pins}
    for sid, share in pinned.items():
        units[sid] = round(pool * share)
    rest = [s for s in active if s["id"] not in pinned]
    left = pool - sum(units[sid] for sid in pinned)
    weights = {s["id"]: _yield(s) + 1 for s in rest}  # +1 so a new Active source still gets a share
    for sid, w in weights.items():
        units[sid] = round(left * w / sum(weights.values()))
    for s in active:
        units[s["id"]] = max(b["min_units_per_active"], units[s["id"]])
    return units
