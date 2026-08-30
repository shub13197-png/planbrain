"""Rough-cut capacity: the load arithmetic, and the plumbing across two grains.

The pure cases state their expected hours in full. This is the engine behind the
one claim a reorder point structurally cannot make, so it should be auditable by
hand.
"""

import pytest

from planbrain import netreq, rccp
from planbrain.demo import build_demo, populate
from planbrain.facts.access import FrozenScenarioError, read_facts
from planbrain.facts.scenario import commit_scenario
from planbrain.rccp import NoPlanError, Routing, compute_load, load_all

BLENDER = 5


def _load(capacity, releases, routings=None, resource_id=BLENDER):
    return compute_load(
        resource_id=resource_id,
        capacity_avail_hours=capacity,
        routings=routings or [Routing(101, BLENDER, hours_per_unit=0.05, setup_hours=1.5)],
        planned_order_release=releases,
    )


# --------------------------------------------------------------------------
# the load arithmetic
# --------------------------------------------------------------------------

def test_load_is_quantity_times_rate_plus_one_setup():
    """200 units at 0.05h plus a 1.5h changeover = 11.5 hours."""
    result = _load([16.0, 16.0], {101: [200.0, 0.0]})
    assert result.capacity_load_hours == [11.5, 0.0]


def test_setup_is_charged_once_per_bucket_not_per_unit():
    """Otherwise a changeover would scale with volume, which is backwards."""
    small = _load([16.0], {101: [1.0]})
    large = _load([16.0], {101: [200.0]})
    assert small.capacity_load_hours[0] == pytest.approx(1.55)
    assert large.capacity_load_hours[0] == pytest.approx(11.5)


def test_a_bucket_with_no_production_carries_no_setup():
    result = _load([16.0, 16.0], {101: [0.0, 0.0]})
    assert result.capacity_load_hours == [0.0, 0.0]


def test_several_skus_accumulate_on_one_resource():
    routings = [
        Routing(101, BLENDER, hours_per_unit=0.05, setup_hours=1.0),
        Routing(102, BLENDER, hours_per_unit=0.10, setup_hours=2.0),
    ]
    result = _load([24.0], {101: [100.0], 102: [50.0]}, routings=routings)
    assert result.capacity_load_hours[0] == pytest.approx(6.0 + 7.0)


def test_a_resource_only_takes_the_work_routed_to_it():
    routings = [
        Routing(101, BLENDER, hours_per_unit=0.05),
        Routing(102, 99, hours_per_unit=10.0),
    ]
    result = _load([16.0], {101: [100.0], 102: [100.0]}, routings=routings)
    assert result.capacity_load_hours[0] == pytest.approx(5.0)


def test_utilisation_is_load_over_available():
    result = _load([16.0], {101: [200.0]})
    assert result.utilisation[0] == pytest.approx(11.5 / 16.0)


def test_an_overloaded_bucket_is_reported():
    result = _load([8.0], {101: [200.0]})
    assert result.overloaded_buckets == [0]
    assert result.utilisation[0] > 1.0


def test_a_bucket_at_exactly_capacity_is_not_overloaded():
    result = _load([11.5], {101: [200.0]})
    assert result.overloaded_buckets == []
    assert result.utilisation[0] == 1.0


# --------------------------------------------------------------------------
# work scheduled where there is no capacity at all
# --------------------------------------------------------------------------

def test_work_on_a_closed_day_is_a_separate_exception():
    """A different mistake from an overload, not a worse degree of one.

    Zero available hours means a closed day or a resource down for maintenance.
    Dividing by zero has no honest answer, so utilisation reads 0.0 and the real
    signal lives in its own list where it cannot be mistaken for a quiet day.
    """
    result = _load([16.0, 0.0], {101: [0.0, 200.0]})
    assert result.load_without_capacity == [1]
    assert result.overloaded_buckets == [1]
    assert result.utilisation == [0.0, 0.0]
    assert result.capacity_load_hours[1] == 11.5


def test_a_closed_day_with_no_work_is_not_an_exception():
    result = _load([16.0, 0.0], {101: [200.0, 0.0]})
    assert result.load_without_capacity == []
    assert result.overloaded_buckets == []


def test_overall_utilisation_of_a_resource_with_no_capacity_is_none():
    """Not zero and not infinity: a resource with no hours has no utilisation,
    and either number would be a statement nobody can act on."""
    result = _load([0.0, 0.0], {101: [200.0, 0.0]})
    assert result.overall_utilisation is None
    assert result.total_load == 11.5


def test_overall_utilisation_spans_the_horizon():
    result = _load([10.0, 10.0], {101: [200.0, 0.0]})
    assert result.overall_utilisation == pytest.approx(11.5 / 20.0)


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------

def test_a_misaligned_release_series_is_refused():
    with pytest.raises(ValueError, match="spine-aligned"):
        _load([16.0, 16.0, 16.0], {101: [1.0, 2.0]})


def test_a_sku_with_no_releases_is_simply_absent():
    result = _load([16.0], {})
    assert result.capacity_load_hours == [0.0]


def test_load_all_covers_every_resource_in_order():
    routings = [
        Routing(101, 1, hours_per_unit=1.0),
        Routing(102, 2, hours_per_unit=2.0),
    ]
    loads = load_all(
        resources={2: [10.0], 1: [10.0]},
        routings=routings,
        planned_order_release={101: [1.0], 102: [1.0]},
    )
    assert [load.resource_id for load in loads] == [1, 2]
    assert loads[0].capacity_load_hours == [1.0]
    assert loads[1].capacity_load_hours == [2.0]


# --------------------------------------------------------------------------
# against the demo, across two fact grains
# --------------------------------------------------------------------------

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


def test_loading_without_a_plan_is_refused(seeded, demo):
    """All-zero releases mean netreq has not run, not that the plant is idle.

    Reporting a comfortably empty factory is the most reassuring possible wrong
    answer.
    """
    with pytest.raises(NoPlanError, match="run planbrain.netreq"):
        rccp.run(seeded, demo)


def test_run_writes_load_at_the_capacity_grain(planned, demo):
    """Reads fact_supply_demand, writes fact_capacity. The multi-grain registry
    established at item 2 pays for itself here."""
    report = rccp.run(planned, demo)
    assert report["rows_written"] > 0

    resource = demo.resources[0].resource_id
    rows = read_facts(
        planned, "fact_capacity",
        scenario_id=0, measure="capacity_load_hours",
        start=demo.horizon_start, end=demo.horizon_end, keys=[(resource,)],
    )
    assert len(rows) == report["buckets"]


def test_the_report_says_whether_the_plan_is_feasible(planned, demo):
    """A row count would not say whether the plant can actually make the plan."""
    report = rccp.run(planned, demo)
    # Behaviour, not type: feasible must agree with the overload lists it
    # summarises, or the headline verdict and the detail could disagree.
    any_over = any(d["overloaded_buckets"] for d in report["resources"].values())
    assert report["feasible"] is not any_over
    for detail in report["resources"].values():
        assert set(detail) == {
            "load_hours", "capacity_hours", "utilisation",
            "overloaded_buckets", "load_without_capacity",
        }


def test_no_work_is_scheduled_on_a_closed_day(planned, demo):
    """Item 6 found twelve such buckets per resource; item 7 fixed the cause.

    netreq now pulls a release back to the previous working bucket, so nothing
    lands on a day the plant is shut. This asserts the bug stays fixed -- it was
    invisible to every engine except rccp.
    """
    report = rccp.run(planned, demo)
    flagged = sum(
        len(detail["load_without_capacity"]) for detail in report["resources"].values()
    )
    assert flagged == 0


def test_cost_based_lot_sizing_cuts_load_substantially(seeded, demo):
    """Lot-for-lot pays a changeover on every day a blend is needed.

    Trading setup against holding is what the Wagner-Whitin DP already did; it
    only ever lacked costs. This is the capacity loop closing -- but see
    test_cost_based_lot_sizing_does_not_reach_feasibility for what it does not do.
    """
    netreq.run(seeded, demo, lot_sizing="as_master")
    as_master = rccp.run(seeded, demo)
    netreq.run(seeded, demo, lot_sizing="cost_based")
    cost_based = rccp.run(seeded, demo)

    load_before = sum(d["load_hours"] for d in as_master["resources"].values())
    load_after = sum(d["load_hours"] for d in cost_based["resources"].values())
    assert load_after < load_before * 0.75


def test_cost_based_lot_sizing_does_not_reach_feasibility(seeded, demo):
    """The stated limit, asserted rather than hoped.

    Cost-based lot sizing reduces load by batching; it never sees a per-bucket
    capacity limit, so it cannot be steered to one. Overall utilisation drops
    comfortably under capacity while individual buckets stay overloaded -- the
    exact signature of cost-based rather than capacity-constrained lot sizing.
    Genuinely capacity-constrained lot sizing is the CLSP and is out of scope.

    If this test ever starts failing because the plan became feasible, that is a
    real result and the README claim can be upgraded. It must not be made to
    pass by tuning.
    """
    netreq.run(seeded, demo, lot_sizing="cost_based")
    report = rccp.run(seeded, demo)

    load = sum(d["load_hours"] for d in report["resources"].values())
    capacity = sum(d["capacity_hours"] for d in report["resources"].values())
    assert load < capacity, "on average the plan fits"

    overloaded = sum(
        len(d["overloaded_buckets"]) for d in report["resources"].values()
    )
    assert overloaded > 0, "but not bucket by bucket"
    assert report["feasible"] is False


def test_run_refuses_a_frozen_scenario(planned, demo):
    committed = commit_scenario(planned, source_scenario_id=0, name="commit")
    with pytest.raises(FrozenScenarioError):
        rccp.run(planned, demo, scenario_id=committed)


def test_rerunning_is_idempotent(planned, demo):
    first = rccp.run(planned, demo)
    second = rccp.run(planned, demo)
    assert first == second
