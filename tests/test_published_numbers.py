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
from pathlib import Path

import pytest

from planbrain import netreq, rccp, simulate
from planbrain.demo import build_demo, populate
from planbrain.forecast import demand_keys
from planbrain.haulplan import Ledger, jain_index
from planbrain.haulplan.assign import assign
from planbrain.haulplan.fairness import ceiling

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
