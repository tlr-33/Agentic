"""Fast checks for the two things that must never break: dedupe and budget rules."""
import yaml

from sourcing import budget
from sourcing.dedupe import StartupIndex, domain_of, duplicate_groups, name_key
from sourcing.scrapers.common import startup_links

CFG = yaml.safe_load(open("config.yaml"))


def rec(i, name, domain=None):
    return {"id": f"rec{i}", "fields": {"Startup": name, **({"Domain": domain} if domain else {})}}


def test_normalizers():
    assert domain_of("https://www.Reserv.com/about") == "reserv.com"
    assert name_key("Clarix Imaging Corporation") == name_key("clarix imaging")


def test_index_merges_same_company_but_not_lookalikes():
    idx = StartupIndex([rec(1, "Nestera", "nesterahealth.com"), rec(2, "Aura Life Science"), rec(3, "Flex")])
    assert idx.find("Nestera Health", "nesterahealth.com")["id"] == "rec1"  # domain wins over name
    assert idx.find("Aura Interactive") is None                              # shared first word only
    assert idx.find("Flex Robotics") is None                                 # 1-word names need an exact match


def test_duplicate_groups():
    rows = [rec(1, "Signl", "signlbio.com"), rec(2, "Signl Bio", "signlbio.com"), rec(3, "Tovie", "tovie.com"),
            rec(4, "Base", "base.com"), rec(5, "Base", "base-two.com")]
    groups = duplicate_groups(rows)
    assert [sorted(r["id"] for r in g) for g in groups] == [["rec1", "rec2"]]  # same name, different sites: apart


def test_link_extractor_keeps_homepages_only():
    html = ('<a href="https://tovie.com/">Tovie</a> <a href="https://techcrunch.com/x/y">Tovie raises</a>'
            '<a href="https://acme.com/blog/post">Read more now</a>'
            '<a href="https://www.linkedin.com/company/beaugene">BeauGene</a>')
    assert [(c.name, c.domain) for c in startup_links(html)] == [("Tovie", "tovie.com"), ("BeauGene", None)]


def src(i, name, status, y=0, runs=0, left=None):
    return {"id": i, "fields": {"Source": name, "Status": status, "Yield (per 10 units)": y, "Runs": runs,
                                "Trial Runs Left": left}}


def test_prune_promote_and_wait_for_pending():
    runnable = {"A", "B", "T", "P"}
    s = [src("a", "A", "Active", 50, 3), src("b", "B", "Active", 0, 3), src("t", "T", "Trial", 4, 2, 0),
         src("p", "P", "Proposed")]
    assert {sid: f["Status"] for sid, f, _ in budget.status_changes(s, CFG, runnable, {})} == \
        {"b": "Pruned", "t": "Active", "p": "Trial"}
    # a source with unjudged candidates is not pruned yet
    assert "b" not in {sid for sid, _, _ in budget.status_changes(s, CFG, runnable, {"b": 3})}


def test_allocation_reserves_explore_slice():
    s = [src("a", "A", "Active", 50), src("n", "N", "Active", 0), src("t", "T", "Trial", 0, 1, 2)]
    units = budget.allocate(s, CFG, {"A", "N", "T"})
    b = CFG["budget"]
    assert units["t"] == min(b["max_units_per_trial"], round(b["units_per_run"] * b["explore_share"]))
    assert units["a"] > units["n"] >= b["min_units_per_active"]
