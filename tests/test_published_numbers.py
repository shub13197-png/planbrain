"""The numbers in README.md and docs/ must match what the code actually produces.

This exists because they stopped matching. When the fleet became derived from
the freight profile instead of randomly generated, one `rng.choice` disappeared
and every later draw in the deterministic stream shifted. The dataset was still
reproducible; it was simply a different dataset, and every figure published from
the old one was quietly wrong.

Nothing failed. The tests all passed, because the tests assert behaviour rather
than particular values. A reader running the demo would have seen numbers that
disagreed with the README and had no way to tell which was right.

Same fix as `test_examples_on_disk_match_their_generator`: pin the published
values, so changing the generator forces a decision about the docs rather than
silently invalidating them.

**When this fails**, the docs are stale, not the code. Re-run the reports and
update both the docs and the constants below.
"""

import sqlite3
import re
from pathlib import Path

import pytest

from planbrain import netreq, rccp, simulate
from planbrain.demo import build_demo, populate
from planbrain.facts.access import read_facts
from planbrain.forecast import demand_keys
from planbrain.haulplan import Ledger, jain_index
from planbrain.haulplan.assign import assign
from planbrain.haulplan.fairness import ceiling
from tools.published import Computed, FIGURES, by_key

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"
SEED = 7

#: Published in README.md and docs/. Tolerances are generous enough to survive a
#: floating-point or library-version wobble and tight enough that a shifted
#: dataset breaks them.
PUBLISHED = {
    "parts": 200,
    "bom_edges": 294,
    "trucks": 15,
    "history_days": 546,
    "demand_series": 222,
    "rccp_utilisation": 0.85,
    "rccp_overloaded_buckets": 139,
    "fairness_opening": 0.8288,
    "fairness_after_greedy": 0.8865,
    "fairness_ceiling": 0.8898,
}


@pytest.fixture(scope="module")
def pipeline():
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    demo = build_demo(seed=SEED)
    populate(con, demo)
    netreq.run(con, demo, lot_sizing="cost_based")
    capacity = rccp.run(con, demo)
    yield con, demo, capacity
    con.close()


@pytest.fixture(scope="module")
def ordered():
    """The path a user actually clicks: forecast, then net against it.

    Its own connection and its own forecast pass, rather than reusing
    `pipeline`, which nets against a replay of last year. The two produce
    genuinely different plans -- 2001 releases against 1843 -- and the figures
    in `docs/orders.md` describe the default the application ships with. Costs a
    full forecast over 222 series; the alternative is a published number nothing
    recomputes.
    """
    from planbrain import forecast, orders

    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    demo = build_demo(seed=SEED)
    populate(con, demo)
    forecast.run(con, demo)
    netreq.run(con, demo, source="forecast", lot_sizing="cost_based")
    yield orders.order_list(con, demo), con, demo
    con.close()


def test_the_order_list_is_the_size_the_docs_publish(ordered):
    """The count of the answer that used to be discarded.

    Pinned because it is the headline of `docs/orders.md`, and because it moves
    with the demo, the forecast models and the lot-sizing rule -- none of which
    would break a behaviour test on their way past.
    """
    rows, con, demo = ordered
    # Through the accessor, not raw SQL: storage is sparse, so a non-zero count
    # off a densified read is the same number, and the exemption for reading a
    # fact table directly stays confined to tests/test_orders.py.
    receipts = sum(
        1 for f in read_facts(
            con, "fact_supply_demand", scenario_id=0,
            measure="planned_order_receipt",
            start=demo.horizon_start, end=demo.horizon_end,
            keys=[(p.sku_id, loc.loc_id) for p in demo.parts for loc in demo.locations],
        ) if f.qty
    )

    assert len(rows) == by_key("orders_total").value
    assert receipts == by_key("orders_receipts").value
    assert sum(1 for o in rows if o.action == "make") == by_key("orders_make").value
    assert sum(1 for o in rows if o.action == "buy") == by_key("orders_buy").value

    # The gap is the claim: more receipts than releases, because _offset merges
    # them onto working buckets. If these ever came out equal, the list could
    # carry a due date per row and docs/orders.md would be wrong to refuse one.
    assert receipts > len(rows)


def test_the_risk_list_is_the_size_the_docs_publish(ordered):
    """Both halves: no shortage, and 212 overdue orders across 159 items.

    The zero matters as much as the 212. The claim in `docs/netreq.md` is that a
    risk screen reading negative projections alone reports nothing wrong on a
    plan that is in trouble, and that is only checkable against a plan which has
    no shortages *and* a long past-due list. Either number moving alone would
    break the claim without breaking any behaviour test.

    On the `ordered` fixture -- forecast, then net -- because that is the path
    the application takes by default. Netting against last year's replay instead
    gives 213, and quoting a figure from a path nobody clicks is the same
    mistake the order list already made once.
    """
    from planbrain import alerts

    _rows, con, demo = ordered
    assert alerts.shortages(con, demo) == []

    overdue = alerts.past_due(con, demo)
    assert len(overdue) == by_key("past_due_orders").value
    assert len({o.sku_id for o in overdue}) == 159


def test_the_dataset_is_the_one_the_docs_describe(pipeline):
    _con, demo, _capacity = pipeline
    assert len(demo.parts) == PUBLISHED["parts"]
    assert len(demo.bom) == PUBLISHED["bom_edges"]
    assert len(demo.trucks) == PUBLISHED["trucks"]
    assert (demo.history_end - demo.history_start).days + 1 == PUBLISHED["history_days"]
    assert len(demand_keys(demo)) == PUBLISHED["demand_series"]


def test_the_capacity_verdict_is_the_one_the_docs_publish(pipeline):
    _con, _demo, capacity = pipeline
    load = sum(d["load_hours"] for d in capacity["resources"].values())
    available = sum(d["capacity_hours"] for d in capacity["resources"].values())
    over = sum(len(d["overloaded_buckets"]) for d in capacity["resources"].values())

    assert load / available == pytest.approx(PUBLISHED["rccp_utilisation"], abs=0.01)
    assert over == PUBLISHED["rccp_overloaded_buckets"]
    assert capacity["feasible"] is False, "the README says NOT FEASIBLE"


def test_the_fairness_figures_are_the_ones_the_docs_publish(pipeline):
    _con, demo, _capacity = pipeline
    opening = list(demo.truck_ytd_long_haul_km.values())
    ledger = Ledger.opening(demo.truck_ytd_long_haul_km)
    assign(demo.trips, demo.trucks, ledger)
    work = sum(t.distance_km for t in demo.trips if t.is_long_haul)

    assert jain_index(opening) == pytest.approx(PUBLISHED["fairness_opening"], abs=0.001)
    assert jain_index(ledger.distribution()) == pytest.approx(
        PUBLISHED["fairness_after_greedy"], abs=0.001
    )
    assert ceiling(opening, work) == pytest.approx(
        PUBLISHED["fairness_ceiling"], abs=0.001
    )


def test_greedy_stays_within_a_whisker_of_the_ceiling(pipeline):
    """The claim that justifies deferring a solver. If the headroom ever grows,
    the deferral needs revisiting rather than restating."""
    _con, demo, _capacity = pipeline
    opening = list(demo.truck_ytd_long_haul_km.values())
    ledger = Ledger.opening(demo.truck_ytd_long_haul_km)
    assign(demo.trips, demo.trucks, ledger)
    work = sum(t.distance_km for t in demo.trips if t.is_long_haul)

    headroom = ceiling(opening, work) - jain_index(ledger.distribution())
    assert 0 <= headroom < 0.01


# --------------------------------------------------------------------------
# the register: pins protect claims, tests protect code
# --------------------------------------------------------------------------


def _suppressed_with_growth(demo) -> int:
    """How many fitted trends a non-zero growth rate displaces.

    Any non-zero rate gives the same count: the suppression depends on which
    series select a trend, not on how large the assumption is.
    """
    import sqlite3

    from planbrain import forecast
    from planbrain.demo import populate
    from planbrain.facts.scenario import set_growth

    root = Path(__file__).resolve().parents[1]
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((root / "planbrain" / "facts" / "schema.sql").read_text(encoding="utf-8"))
    populate(con, demo)
    set_growth(con, scenario_id=0, demand_growth_pct=5.0)
    return forecast.run(con, demo)["trends_suppressed"]


def test_every_published_figure_appears_where_it_says_it_does():
    """register -> docs. A doc that drifts from the register fails.

    This is the link the behaviour tests cannot provide. 449 passing tests did
    not notice when every published number went stale, because the behaviour had
    not changed -- only the dataset had.
    """
    root = Path(__file__).resolve().parents[1]
    missing = []
    for figure in FIGURES:
        for where in figure.where:
            text = (root / where).read_text(encoding="utf-8")
            if figure.literal not in text:
                missing.append(f"{figure.key}: {figure.literal!r} not in {where}")
    assert missing == [], "\n".join(missing)


def test_every_published_figure_still_matches_a_fresh_run(pipeline):
    """register -> code. The other half of the chain.

    Recomputes the whole portfolio, so it is the slowest test here and the only
    one that would have caught the staleness. Worth its runtime: the alternative
    is a reader catching it.
    """
    con, demo, capacity = pipeline

    report = simulate.compare(con, demo, keys=demand_keys(demo), safety_days=7.0)
    policies = report["policies"]

    def pattern(policy, name):
        return policies[policy].by_pattern[name]["fill_rate"].value

    load = sum(d["load_hours"] for d in capacity["resources"].values())
    available = sum(d["capacity_hours"] for d in capacity["resources"].values())

    ledger = Ledger.opening(demo.truck_ytd_long_haul_km)
    opening = list(demo.truck_ytd_long_haul_km.values())
    assign(demo.trips, demo.trucks, ledger)
    work = sum(t.distance_km for t in demo.trips if t.is_long_haul)

    computed = Computed({
        "parts": len(demo.parts),
        "demand_series": len(demand_keys(demo)),
        "history_days": (demo.history_end - demo.history_start).days + 1,
        # A separate run with growth set, because the suppression count is only
        # produced when the one-source-of-trend rule is active. Costs a second
        # forecast pass over the portfolio; the alternative is a published
        # number nothing recomputes, which is the failure this register exists
        # for.
        "trends_suppressed": _suppressed_with_growth(demo),
        # A second full replay, at a service level rather than days of cover.
        # Expensive, and the alternative is a published table nothing
        # recomputes -- which is the failure this register exists for.
        "service_level_95_fill": simulate.compare(
            con, demo, keys=demand_keys(demo), safety_service_level=0.95
        )["policies"]["forecast"].fill_rate.value,
        "fitted_fill": policies["forecast"].fill_rate.value,
        "tuned_fill": policies["reorder_point"].fill_rate.value,
        "stale_fill": policies["reorder_point_stale"].fill_rate.value,
        "naive_fill": policies["naive_zero"].fill_rate.value,
        "intermittent_fitted": pattern("forecast", "intermittent"),
        "intermittent_tuned": pattern("reorder_point", "intermittent"),
        "lumpy_fitted": pattern("forecast", "lumpy"),
        "lumpy_tuned": pattern("reorder_point", "lumpy"),
        "naive_intermittent": pattern("naive_zero", "intermittent"),
        "naive_lumpy": pattern("naive_zero", "lumpy"),
        "rccp_utilisation": load / available,
        "rccp_overloaded": sum(
            len(d["overloaded_buckets"]) for d in capacity["resources"].values()
        ),
        "fairness_opening": jain_index(opening),
        "fairness_after": jain_index(ledger.distribution()),
        "fairness_ceiling": ceiling(opening, work),
    })

    drifted = [
        f"{f.key}: docs say {f.value}, a fresh run gives {actual:.4f}"
        for f, actual in computed.mismatches()
    ]
    assert drifted == [], (
        "Published figures are stale. The docs are wrong, not the code -- "
        "re-run the reports and update tools/published.py and the prose.\n"
        + "\n".join(drifted)
    )


def test_the_register_covers_the_headline_claims():
    """A figure removed from the register stops being checked. The claims that
    matter most must stay covered."""
    keys = {f.key for f in FIGURES}
    for required in ("fitted_fill", "tuned_fill", "intermittent_fitted",
                     "lumpy_fitted", "lumpy_tuned", "naive_intermittent"):
        assert required in keys


def test_service_claims_are_not_quoted_from_a_sample():
    """The README's service table must be the whole portfolio.

    An earlier version quoted a 40-series sample and claimed lumpy demand was
    where the tool was clearly ahead. On all 222 series a tuned reorder point
    beats it there. The sample was the difference, so the docs now say --sample 0
    and this pins that.
    """
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )
    assert "--sample 0" in readme
    assert "All 222 series" in readme
    assert "What changed when we stopped sampling" in readme


def test_the_service_range_brackets_the_published_headline(pipeline):
    """Both numbers, because the unconstrained one assumes a plan rccp calls
    infeasible. A single figure here would be the thing the README warns about."""
    con, demo, _capacity = pipeline
    keys = demand_keys(demo)
    sample = [keys[int(i * len(keys) / 24)] for i in range(24)]

    plain = simulate.compare(con, demo, keys=sample, safety_days=7.0)
    capped = simulate.compare(
        con, demo, keys=sample, safety_days=7.0,
        delivery_factor=simulate.capacity_factor(con, demo),
    )
    fitted = plain["policies"]["forecast"].fill_rate.value
    constrained = capped["policies"]["forecast"].fill_rate.value

    assert 0.95 < fitted < 1.0
    assert constrained <= fitted
    assert plain["policies"]["forecast"].average_on_hand.value > 0


def test_the_fitted_forecast_still_beats_both_reorder_points(pipeline):
    """The README's central comparison. If this inverts, the claims change."""
    con, demo, _capacity = pipeline
    keys = demand_keys(demo)
    sample = [keys[int(i * len(keys) / 24)] for i in range(24)]
    report = simulate.compare(con, demo, keys=sample, safety_days=7.0)

    fitted = report["policies"]["forecast"].fill_rate.value
    tuned = report["policies"]["reorder_point"].fill_rate.value
    stale = report["policies"]["reorder_point_stale"].fill_rate.value
    naive = report["policies"]["naive_zero"].fill_rate.value

    # Ordering only. The MAGNITUDE of each gap is sample-dependent -- that is
    # exactly what went wrong in the README -- so the published figures come
    # from the full portfolio via `--sample 0` and are not asserted here on a
    # 24-series sample.
    assert fitted > tuned > stale > naive


def test_no_document_publishes_a_test_count():
    """A count of tests is a number that goes stale on the next commit that adds
    one, and it was stale in two places at once -- the README claiming 604 in one
    line and 625 in another, against a suite that had grown past both.

    It also says nothing. "604 tests pass" is not evidence about the product; the
    claims that carry weight are the fill rates in the register above, which are
    pinned to a fresh run. So the rule is that prose does not quote a suite size
    at all, rather than that the quoted size must be kept accurate -- there is no
    version of this number worth the maintenance.

    Numbers of *things the product handles* are fine and are not matched here;
    this looks only for a count immediately qualifying the word "tests".
    """
    root = Path(__file__).resolve().parents[1]
    pattern = re.compile(r"\b\d{2,}\s+(?:passing\s+)?tests\b")
    offenders = []
    for path in [root / "README.md", *sorted((root / "docs").rglob("*.md"))]:
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    assert offenders == [], (
        "these publish a test count, which rots on the next commit:\n"
        + "\n".join(offenders)
    )
