"""netreq against the demo dataset, through the accessors.

The arithmetic is tested in test_netreq_math.py against textbook fixtures. This
file tests the plumbing only: that the seam resolves, that outputs land in the
right measures, and that the derived-measure rule actually holds.
"""

import pytest

from planbrain import netreq
from planbrain.demo import build_demo, populate
from planbrain.facts.access import FrozenScenarioError, read_facts
from planbrain.facts.scenario import commit_scenario
from planbrain.netreq import GrossReqSourceError, resolve_gross_req

PLANT = 1


@pytest.fixture(scope="module")
def demo():
    return build_demo(seed=7)


@pytest.fixture
def seeded(con, demo):
    populate(con, demo)
    return con


@pytest.fixture
def planned(seeded, demo):
    netreq.run(seeded, demo)
    return seeded


def _series(con, measure, sku_id, demo, loc_id=PLANT, scenario_id=0):
    return [
        r.qty for r in read_facts(
            con, "fact_supply_demand",
            scenario_id=scenario_id, measure=measure,
            start=demo.horizon_start, end=demo.horizon_end, keys=[(sku_id, loc_id)],
        )
    ]


# --------------------------------------------------------------------------
# the seam
# --------------------------------------------------------------------------

def test_forecast_source_is_refused_until_item_four(seeded, demo):
    """Better a loud error than silently netting against a measure nobody writes."""
    with pytest.raises(GrossReqSourceError, match="item 4"):
        resolve_gross_req(
            seeded, scenario_id=0,
            sku_ids=[p.sku_id for p in demo.parts],
            loc_ids=[loc.loc_id for loc in demo.locations],
            horizon_start=demo.horizon_start, horizon_end=demo.horizon_end,
            source="forecast",
        )


def test_unknown_source_is_refused(seeded, demo):
    with pytest.raises(GrossReqSourceError, match="unknown"):
        resolve_gross_req(
            seeded, scenario_id=0, sku_ids=[3000], loc_ids=[11],
            horizon_start=demo.horizon_start, horizon_end=demo.horizon_end,
            source="crystal_ball",
        )


def test_naive_replay_returns_horizon_length_series(seeded, demo):
    demand = resolve_gross_req(
        seeded, scenario_id=0,
        sku_ids=[p.sku_id for p in demo.parts],
        loc_ids=[loc.loc_id for loc in demo.locations],
        horizon_start=demo.horizon_start, horizon_end=demo.horizon_end,
    )
    buckets = (demo.horizon_end - demo.horizon_start).days + 1
    assert demand
    assert all(len(series) == buckets for series in demand.values())


def test_naive_replay_aggregates_across_depots(seeded, demo):
    """Explosion runs at one location, so depot demand is summed before netting."""
    demand = resolve_gross_req(
        seeded, scenario_id=0,
        sku_ids=[p.sku_id for p in demo.parts],
        loc_ids=[loc.loc_id for loc in demo.locations],
        horizon_start=demo.horizon_start, horizon_end=demo.horizon_end,
    )
    multi_depot = [
        sku for sku in demand
        if len({f.keys[1] for f in demo.facts[("fact_supply_demand", "demand_actual")]
                if f.keys[0] == sku}) > 1
    ]
    assert multi_depot, "the demo should have SKUs stocked at more than one depot"


# --------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------

def test_run_writes_every_derived_measure(seeded, demo):
    counts = netreq.run(seeded, demo)
    assert set(counts) == set(netreq.OUTPUT_MEASURES)
    assert counts["gross_req"] > 0
    assert counts["planned_order_release"] > 0


def test_explosion_reaches_raw_materials(planned, demo):
    """A raw material has no independent demand, so any requirement it has
    arrived through two BOM levels."""
    raws = [p.sku_id for p in demo.parts if p.level == "raw"]
    with_demand = [s for s in raws if any(_series(planned, "gross_req", s, demo))]
    assert with_demand, "no raw material saw dependent demand; explosion is broken"


def test_outputs_read_back_dense(planned, demo):
    buckets = (demo.horizon_end - demo.horizon_start).days + 1
    for measure in netreq.OUTPUT_MEASURES:
        series = _series(planned, measure, 3000, demo)
        assert len(series) == buckets


def test_a_level_stores_denser_than_a_flow(planned, demo):
    """Empirically confirms the claim in docs/contracts/facts.md.

    projected_on_hand carries across buckets; gross_req happens in one. If this
    ever inverts, the partitioning guidance in that document is wrong.
    """
    counts = {}
    for measure in ("gross_req", "projected_on_hand"):
        counts[measure] = sum(
            1 for p in demo.parts
            for q in _series(planned, measure, p.sku_id, demo) if q != 0
        )
    assert counts["projected_on_hand"] > counts["gross_req"]


def test_rerunning_is_idempotent(seeded, demo):
    """A second run must not double-count or leave stale rows behind."""
    first = netreq.run(seeded, demo)
    before = {
        m: _series(seeded, m, 3000, demo) for m in netreq.OUTPUT_MEASURES
    }
    second = netreq.run(seeded, demo)
    after = {
        m: _series(seeded, m, 3000, demo) for m in netreq.OUTPUT_MEASURES
    }
    assert first == second
    assert before == after


# --------------------------------------------------------------------------
# the rules that must hold at the boundary
# --------------------------------------------------------------------------

def test_run_never_writes_an_imported_measure(seeded, demo):
    """derived=0 is not enforced by the database, so it is enforced here."""
    assert "demand_actual" not in netreq.OUTPUT_MEASURES
    assert "scheduled_receipt" not in netreq.OUTPUT_MEASURES

    before = _series(seeded, "demand_actual", 3000, demo, loc_id=11)
    netreq.run(seeded, demo)
    assert _series(seeded, "demand_actual", 3000, demo, loc_id=11) == before


def test_write_plans_refuses_an_imported_measure(seeded, demo):
    from planbrain.netreq import adapters

    with pytest.raises(ValueError, match="derived=0"):
        adapters._assert_all_derived(seeded, ("demand_actual",))


def test_run_refuses_a_frozen_scenario(seeded, demo):
    committed = commit_scenario(seeded, source_scenario_id=0, name="commit")
    with pytest.raises(FrozenScenarioError):
        netreq.run(seeded, demo, scenario_id=committed)
