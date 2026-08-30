"""The seeded demo dataset: a fake blending plant with messy history.

This is build item 2, and items 3-5 are tested against it, so its properties are
part of the contract rather than incidental. In particular the history has to be
genuinely messy -- intermittent series, launches, discontinuations, stockouts --
because a forecast backtest against clean synthetic data proves nothing.

Note that none of these tests touch a fact table directly. The dataset is
written and read entirely through the accessor, which is the point.
"""

from datetime import timedelta

import pytest

from planbrain.demo import build_demo, populate
from planbrain.facts.access import FrozenScenarioError, read_facts
from planbrain.facts.scenario import commit_scenario

SEED = 7


@pytest.fixture(scope="module")
def demo():
    return build_demo(seed=SEED)


@pytest.fixture
def populated(con, demo):
    populate(con, demo)
    return con


# --------------------------------------------------------------------------
# reproducibility
# --------------------------------------------------------------------------

def test_is_deterministic(demo):
    """The generator is the dataset. Two runs must be byte-identical."""
    again = build_demo(seed=SEED)
    assert again.parts == demo.parts
    assert again.bom == demo.bom
    assert again.facts == demo.facts


def test_a_different_seed_gives_a_different_dataset(demo):
    other = build_demo(seed=SEED + 1)
    assert other.facts != demo.facts


# --------------------------------------------------------------------------
# shape
# --------------------------------------------------------------------------

def test_has_two_hundred_skus(demo):
    assert len(demo.parts) == 200
    assert len({p.sku_id for p in demo.parts}) == 200


def test_the_fleet_is_sized_from_the_freight_profile(demo):
    """Item 9 found six of twelve trucks unable to take a 12,000 kg truckload,
    because capacities were random and the truckload was set separately."""
    from planbrain.demo.generate import TRUCKLOAD_KG

    assert demo.trucks
    heaviest = max(t.load_kg for t in demo.trips)
    assert max(t.capacity_kg for t in demo.trucks) >= heaviest


def test_a_third_of_the_fleet_cannot_take_a_full_load(demo):
    """Deliberate. A fleet where every truck can do every trip removes the
    feasibility filtering entirely and the ledger becomes a round-robin."""
    from planbrain.demo.generate import MIN_SMALL_SHARE, TRUCKLOAD_KG

    small = [t for t in demo.trucks if t.capacity_kg < TRUCKLOAD_KG]
    assert len(small) / len(demo.trucks) >= MIN_SMALL_SHARE - 1e-9


def test_enough_capable_trucks_for_the_busiest_bucket(demo):
    from collections import Counter

    from planbrain.demo.generate import TRUCKLOAD_KG

    full_loads = Counter(t.bucket for t in demo.trips if t.load_kg >= TRUCKLOAD_KG)
    capable = sum(1 for t in demo.trucks if t.capacity_kg >= TRUCKLOAD_KG)
    assert capable >= max(full_loads.values())


def test_has_a_plant_and_depots(demo):
    kinds = [loc.kind for loc in demo.locations]
    assert kinds.count("plant") == 1
    assert kinds.count("depot") >= 2


def test_history_spans_eighteen_months(demo):
    days = (demo.history_end - demo.history_start).days + 1
    assert 540 <= days <= 555, f"18 months is ~548 days, got {days}"


def test_forward_horizon_follows_history_without_a_gap(demo):
    assert demo.horizon_start == demo.history_end + timedelta(days=1)


# --------------------------------------------------------------------------
# bill of materials
# --------------------------------------------------------------------------

def test_bom_has_three_levels(demo):
    assert {p.level for p in demo.parts} == {"raw", "intermediate", "finished"}


def test_bom_is_acyclic(demo):
    """netreq explodes this. A cycle would not fail, it would not terminate."""
    children = {}
    for edge in demo.bom:
        children.setdefault(edge.parent_sku_id, []).append(edge.child_sku_id)

    WHITE, GREY, BLACK = 0, 1, 2
    colour = {}

    def visit(node):
        colour[node] = GREY
        for child in children.get(node, ()):
            assert colour.get(child, WHITE) != GREY, f"cycle through {child}"
            if colour.get(child, WHITE) == WHITE:
                visit(child)
        colour[node] = BLACK

    for node in list(children):
        if colour.get(node, WHITE) == WHITE:
            visit(node)


def test_every_finished_good_explodes_down_to_raw_materials(demo):
    children = {}
    for edge in demo.bom:
        children.setdefault(edge.parent_sku_id, []).append(edge.child_sku_id)
    level = {p.sku_id: p.level for p in demo.parts}

    def reaches_raw(node):
        kids = children.get(node, ())
        if not kids:
            return level[node] == "raw"
        return any(reaches_raw(k) for k in kids)

    finished = [p.sku_id for p in demo.parts if p.level == "finished"]
    assert all(reaches_raw(s) for s in finished)


def test_bom_quantities_are_positive(demo):
    assert all(e.qty_per > 0 for e in demo.bom)


# --------------------------------------------------------------------------
# the history is actually messy
# --------------------------------------------------------------------------

def _demand_by_series(demo):
    by_series = {}
    for fact in demo.facts[("fact_supply_demand", "demand_actual")]:
        by_series.setdefault(fact.keys, []).append(fact.qty)
    return by_series


def test_demand_is_non_negative(demo):
    assert all(f.qty >= 0 for f in demo.facts[("fact_supply_demand", "demand_actual")])


def test_contains_intermittent_series(demo):
    """Croston and TSB exist for these. Without them item 4 proves nothing."""
    span = (demo.history_end - demo.history_start).days + 1
    by_series = _demand_by_series(demo)
    # Stored facts are sparse, so a short list means many zero days.
    intermittent = [k for k, v in by_series.items() if len(v) < span * 0.4]
    assert len(intermittent) >= 10, f"only {len(intermittent)} intermittent series"


def test_contains_dense_fast_moving_series(demo):
    """Both ends of the spectrum, or a reconciliation test has nothing to tie."""
    span = (demo.history_end - demo.history_start).days + 1
    by_series = _demand_by_series(demo)
    dense = [k for k, v in by_series.items() if len(v) > span * 0.7]
    assert len(dense) >= 10


def test_contains_launches_and_discontinuations(demo):
    """A SKU that starts or stops mid-history breaks naive mean forecasting."""
    assert len(demo.launched_mid_history) >= 3
    assert len(demo.discontinued_mid_history) >= 3


def test_contains_sustained_demand_drift(demo):
    """Lifecycle events are steps; drift is a trend, and only drift makes a
    set-once reorder point go stale. Without it, item 5's stale comparator was
    under-evidenced."""
    assert len(demo.drifting_series) >= 30
    changes = [c for _key, c in demo.drifting_series]
    assert max(changes) > 0.5, "some series should grow substantially"
    assert min(changes) < -0.2, "some series should decay"


def test_drift_is_deterministic(demo):
    assert build_demo(seed=SEED).drifting_series == demo.drifting_series


# --------------------------------------------------------------------------
# capacity sized from demand, per docs/capacity-sizing.md
# --------------------------------------------------------------------------

def test_every_routed_resource_gets_capacity(demo):
    routed = {r.resource_id for r in demo.routings}
    for resource_id in routed:
        assert demo.resource_day_hours[resource_id] > 0


def test_a_resource_with_no_routed_work_gets_no_capacity(demo):
    """The QC lab: nothing routes to it, so sizing gives it nothing. Reported
    honestly by rccp rather than given arbitrary hours."""
    routed = {r.resource_id for r in demo.routings}
    unrouted = [r.resource_id for r in demo.resources if r.resource_id not in routed]
    assert unrouted
    for resource_id in unrouted:
        assert demo.resource_day_hours[resource_id] == 0


def test_capacity_lands_on_the_stated_target_utilisation(demo):
    """Reconstructs the sizing rule independently and checks the result.

    This is the test that stops the rule drifting into "whatever made the plan
    feasible": required hours over available hours must come out at the target
    written in docs/capacity-sizing.md, not at whatever the plan happened to need.
    """
    from planbrain.demo.generate import (
        CAMPAIGN_CYCLE_DAYS,
        DAY_SHAPE,
        TARGET_UTILISATION,
    )

    span = (demo.history_end - demo.history_start).days + 1
    annual = {}
    for fact in demo.facts[("fact_supply_demand", "demand_actual")]:
        annual[fact.keys[0]] = annual.get(fact.keys[0], 0.0) + fact.qty * 365.0 / span
    children = {}
    for edge in demo.bom:
        children.setdefault(edge.parent_sku_id, []).append(edge)
    for level in ("finished", "intermediate"):
        for part in demo.parts:
            if part.level != level:
                continue
            for edge in children.get(part.sku_id, ()):
                annual[edge.child_sku_id] = (
                    annual.get(edge.child_sku_id, 0.0)
                    + annual.get(part.sku_id, 0.0) * edge.qty_per
                )

    required = {}
    for routing in demo.routings:
        hours = annual.get(routing.sku_id, 0.0) * routing.hours_per_unit
        hours += (365.0 / CAMPAIGN_CYCLE_DAYS) * routing.setup_hours
        required[routing.resource_id] = required.get(routing.resource_id, 0.0) + hours

    full_days = 52.0 * sum(DAY_SHAPE)
    for resource_id, need in required.items():
        available = demo.resource_day_hours[resource_id] * full_days
        assert need / available == pytest.approx(TARGET_UTILISATION, rel=1e-3)


def test_the_target_is_not_full_utilisation(demo):
    """A plant that is never tight has no use for rough-cut, and a perfectly
    balanced demo would make the capacity feature look unnecessary."""
    from planbrain.demo.generate import TARGET_UTILISATION

    assert 0.75 <= TARGET_UTILISATION <= 0.90


def test_capacity_is_zero_on_closed_days(demo):
    facts = demo.facts[("fact_capacity", "capacity_avail_hours")]
    closed = [f for f in facts if not demo.calendar.is_working(f.bucket_date)]
    assert closed
    assert all(f.qty == 0 for f in closed)


def test_contains_stockout_windows(demo):
    """Zero demand that is censored supply, not real demand. Item 4 must not learn it."""
    assert len(demo.stockout_windows) >= 5


def test_series_are_spread_across_depots(demo):
    locs = {fact.keys[1] for fact in demo.facts[("fact_supply_demand", "demand_actual")]}
    assert len(locs) >= 2


# --------------------------------------------------------------------------
# multi-grain coverage
# --------------------------------------------------------------------------

def test_capacity_grain_is_populated(demo):
    """Items 2-4 would otherwise all run against one grain, leaving the
    registry untested by real data for three build items."""
    capacity = demo.facts[("fact_capacity", "capacity_avail_hours")]
    assert capacity
    assert {f.keys[0] for f in capacity} == {r.resource_id for r in demo.resources}


def test_fleet_grain_stays_empty(demo):
    """fact_fleet is reserved. Nothing may write to it before haulplan."""
    assert not any(table == "fact_fleet" for table, _ in demo.facts)


def test_supply_side_history_is_present(demo):
    assert demo.facts[("fact_supply_demand", "scheduled_receipt")]


# --------------------------------------------------------------------------
# it lands in the database correctly
# --------------------------------------------------------------------------

def test_populate_reports_what_it_wrote(con, demo):
    counts = populate(con, demo)
    assert counts[("fact_supply_demand", "demand_actual")] > 10_000
    assert counts[("fact_capacity", "capacity_avail_hours")] > 0


def test_populate_stores_no_zero_rows(con, demo):
    """Sparsity survives the round trip: rows written must be fewer than cells."""
    counts = populate(con, demo)
    span = (demo.history_end - demo.history_start).days + 1
    series = len({f.keys for f in demo.facts[("fact_supply_demand", "demand_actual")]})
    dense_cells = span * series
    assert counts[("fact_supply_demand", "demand_actual")] < dense_cells


def test_reads_back_dense_through_the_accessor(populated, demo):
    sku, loc = next(iter(
        {f.keys for f in demo.facts[("fact_supply_demand", "demand_actual")]}
    ))
    rows = read_facts(
        populated, "fact_supply_demand",
        scenario_id=0, measure="demand_actual",
        start=demo.history_start, end=demo.history_end, keys=[(sku, loc)],
    )
    span = (demo.history_end - demo.history_start).days + 1
    assert len(rows) == span, "the reader densifies even though storage is sparse"
    assert any(r.qty > 0 for r in rows)


def test_capacity_reads_back_through_the_accessor(populated, demo):
    resource = demo.resources[0].resource_id
    rows = read_facts(
        populated, "fact_capacity",
        scenario_id=0, measure="capacity_avail_hours",
        start=demo.horizon_start, end=demo.horizon_end, keys=[(resource,)],
    )
    assert any(r.qty > 0 for r in rows)
    assert any(r.qty == 0 for r in rows), "closed days should read as zero, not be missing"


def test_populate_refuses_a_frozen_scenario(con, demo):
    committed = commit_scenario(con, source_scenario_id=0, name="commit")
    with pytest.raises(FrozenScenarioError):
        populate(con, demo, scenario_id=committed)
