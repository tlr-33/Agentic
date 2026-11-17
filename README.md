# Polsky Sourcing Tool

Finds every UChicago-affiliated startup, verifies it, and keeps its profile current in the
**Polsky Sourcing Project** Airtable base. Runs weekly (Mondays) on GitHub Actions.

## How it works

| Layer | Code | What it does | Claude API |
|---|---|---|---|
| 1. Scrapers | `sourcing/scrapers/` | Pull new Polsky posts, cohort/demo-day lists and the Polsky directory into **Mentions**; new names become **Pending** startups | No |
| 2. Manager | `manager.py`, `budget.py` | Merges duplicates; prunes low-yield sources and trials new ones; verifies "real startup?" and "UChicago-affiliated?" | Verification only |
| 3. Research | `research.py`, `logos.py` | Logo taken from each startup's own website; fills profile fields for rows Airtable marks **Research Due** | Profile fields only |

`run.py` runs the layers in order and writes one row to **Run Log**.

## Rules we hold to

- **One row per company.** Matched on website domain, then normalized name; duplicates are merged.
- **No value without a source link.** Affiliation, funding and founding year need an evidence URL or stay blank. Flags are Airtable formulas, not LLM text.
- **Only "Verified startup" rows** reach the gallery. Licensees with no UChicago founder are not UChicago startups.
- **No LinkedIn, X or TikTok scraping** (terms of service).
- **Evaluation:** recall against Polsky's ~650-company alumni list, held out; never tuned against.

## Change the budget

Edit the `budget:` block in `config.yaml`. Source status (Active / Trial / Proposed / Pruned) is set in the
Airtable **Sources** table. To add a new source, add a scraper to `sourcing/scrapers/` under the source's Airtable name.

## Setup

1. Repo **Settings → Secrets → Actions**: add `AIRTABLE_TOKEN`, `AIRTABLE_BASE_ID`, and `ANTHROPIC_API_KEY` (optional; without it, Layers 2–3 skip their LLM steps).
2. **Actions → nightly-sourcing → Run workflow** (tick *dry run* to preview), or wait for the Monday 5am CT run.

Local: `pip install -r requirements.txt`, export the same variables, `python run.py --dry-run`.
