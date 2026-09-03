"""Explaining an overload without pretending to solve it.

The value of this module is entirely in what it refuses to say. It attributes
hours and locates slack; it does not tell anyone what to move, because moving
production earlier needs components earlier and capacity planning cannot see
whether they are there. Most of these tests are about that line holding.
"""

import sqlite3
from pathlib import Path

import pytest

from planbrain.rccp.core import Routing, compute_load
from planbrain.rccp.relief import explain_overload, summarise

ROOT = Path(__file__).resolve().parents[1]


def _routing(sku, hours_per_unit=1.0, setup=0.0, resource=1):
    return Routing(sku_id=sku, resource_id=resource,
                   hours_per_unit=hours_per_unit, setup_hours=setup)


# --------------------------------------------------------------------------
# it agrees with the thing it is explaining
# --------------------------------------------------------------------------

def test_the_contributors_add_up_to_the_load_that_rccp_reports():
    """The one invariant that must never break. A breakdown that disagrees with
    its own total is worse than no breakdown: it looks like detail."""
    capacity = [10.0, 10.0, 10.0]
    routings = [_routing(1, 2.0, setup=1.0), _routing(2, 3.0)]
    releases = {1: [4.0, 0.0, 1.0], 2: [2.0, 1.0, 0.0]}

    load = compute_load(resource_id=1, capacity_avail_hours=capacity,
                        routings=routings, planned_order_release=releases)
    overloads = explain_overload(resource_id=1, capacity_avail_hours=capacity,
                                 routings=routings, planned_order_release=releases)

    assert [o.bucket for o in overloads] == load.overloaded_buckets
    for overload in overloads:
        total = sum(c.hours for c in overload.contributors)
        assert total == pytest.approx(load.capacity_load_hours[overload.bucket])


def test_setup_hours_are_separated_from_run_hours():
    """A bucket over by two hours because of four changeovers is a different
    problem from one over by two hours of running, and the fix is different."""
    capacity = [5.0]
    routings = [_routing(1, hours_per_unit=1.0, setup=3.0)]
    overloads = explain_overload(resource_id=1, capacity_avail_hours=capacity,
                                 routings=routings, planned_order_release={1: [4.0]})

    contributor = overloads[0].contributors[0]
    assert contributor.setup_hours == 3.0
    assert contributor.run_hours == 4.0
    assert contributor.hours == 7.0


def test_contributors_are_ranked_by_hours():
    """A planner reads the top of the list, so the top of the list has to be the
    biggest thing."""
    capacity = [5.0]
    routings = [_routing(1, 1.0), _routing(2, 4.0), _routing(3, 2.0)]
    releases = {1: [1.0], 2: [1.0], 3: [1.0]}
    overloads = explain_overload(resource_id=1, capacity_avail_hours=capacity,
                                 routings=routings, planned_order_release=releases)
    assert [c.sku_id for c in overloads[0].contributors] == [2, 3, 1]


def test_a_bucket_that_fits_is_not_reported():
    overloads = explain_overload(resource_id=1, capacity_avail_hours=[10.0, 10.0],
                                 routings=[_routing(1, 1.0)],
                                 planned_order_release={1: [5.0, 5.0]})
    assert overloads == []


def test_work_scheduled_where_there_is_no_capacity_at_all_is_reported():
    """The more serious case, and the one a ratio cannot express: dividing by
    zero available hours has no honest answer, so it must not be silently
    skipped."""
    overloads = explain_overload(resource_id=1, capacity_avail_hours=[0.0],
                                 routings=[_routing(1, 1.0)],
                                 planned_order_release={1: [3.0]})
    assert len(overloads) == 1
    assert overloads[0].over_hours == 3.0


# --------------------------------------------------------------------------
# slack, and what it does not promise
# --------------------------------------------------------------------------

def test_slack_looks_both_ways_within_the_window():
    capacity = [10.0, 10.0, 10.0, 10.0, 10.0]
    routings = [_routing(1, 1.0)]
    releases = {1: [2.0, 0.0, 14.0, 0.0, 3.0]}
    overloads = explain_overload(resource_id=1, capacity_avail_hours=capacity,
                                 routings=routings, planned_order_release=releases,
                                 window=2)
    over = overloads[0]
    assert over.bucket == 2
    assert over.slack_before == pytest.approx(8.0 + 10.0)   # buckets 0 and 1
    assert over.slack_after == pytest.approx(10.0 + 7.0)    # buckets 3 and 4


def test_the_window_bounds_how_far_it_looks():
    capacity = [10.0] * 6
    routings = [_routing(1, 1.0)]
    releases = {1: [0.0, 0.0, 0.0, 0.0, 0.0, 16.0]}
    narrow = explain_overload(resource_id=1, capacity_avail_hours=capacity,
                              routings=routings, planned_order_release=releases,
                              window=1)[0]
    wide = explain_overload(resource_id=1, capacity_avail_hours=capacity,
                            routings=routings, planned_order_release=releases,
                            window=5)[0]
    assert narrow.slack_before == 10.0
    assert wide.slack_before == 50.0


def test_a_negative_window_is_refused():
    with pytest.raises(ValueError, match="window"):
        explain_overload(resource_id=1, capacity_avail_hours=[1.0],
                         routings=[], planned_order_release={}, window=-1)


def test_relief_is_false_when_the_plant_is_genuinely_short():
    """False is the informative direction: no rescheduling inside the window
    helps, so this is a capacity decision rather than a scheduling one."""
    capacity = [10.0, 10.0]
    routings = [_routing(1, 1.0)]
    releases = {1: [10.0, 40.0]}          # bucket 0 exactly full, bucket 1 30h over
    over = explain_overload(resource_id=1, capacity_avail_hours=capacity,
                            routings=routings, planned_order_release=releases)[0]
    assert over.slack_before == 0.0
    assert over.relieved_by_moving_earlier is False


# --------------------------------------------------------------------------
# the summary, and the honesty of its two headline numbers
# --------------------------------------------------------------------------

def test_concentration_says_whether_this_is_one_product_or_the_plant():
    """The number that decides which conversation a planner has. One SKU at 60%
    is a product problem; every SKU at 4% is a capacity problem."""
    capacity = [10.0]
    routings = [_routing(1, 1.0), _routing(2, 1.0)]
    hog = summarise(explain_overload(
        resource_id=1, capacity_avail_hours=capacity, routings=routings,
        planned_order_release={1: [19.0], 2: [1.0]},
    ))
    assert hog["worst_sku"] == 1
    assert hog["concentration"] == pytest.approx(0.95)

    even = summarise(explain_overload(
        resource_id=1, capacity_avail_hours=capacity, routings=routings,
        planned_order_release={1: [10.0], 2: [10.0]},
    ))
    assert even["concentration"] == pytest.approx(0.5)


def test_the_relievable_count_is_named_an_upper_bound():
    """Neighbouring overloaded buckets are each measured against the same spare
    hours and cannot all use it. The key says so, because a reader who saw
    "relievable: 131 of 139" would reasonably conclude the plant is fine."""
    capacity = [10.0, 10.0, 10.0]
    routings = [_routing(1, 1.0)]
    #    one quiet day, then two overloaded ones both looking back at it
    releases = {1: [0.0, 15.0, 15.0]}
    summary = summarise(explain_overload(
        resource_id=1, capacity_avail_hours=capacity, routings=routings,
        planned_order_release=releases, window=3,
    ))
    assert "relievable_by_moving_earlier_upper_bound" in summary
    assert "relievable_by_moving_earlier" not in summary
    assert summary["relievable_by_moving_earlier_upper_bound"] == 2, (
        "both buckets claim the same 10 spare hours, which is exactly why this "
        "is an upper bound"
    )


def test_an_empty_overload_list_summarises_to_nothing_rather_than_failing():
    """A feasible plan is the good case and must not raise on the way to being
    reported."""
    summary = summarise([])
    assert summary["buckets"] == 0
    assert summary["concentration"] is None


# --------------------------------------------------------------------------
# end to end
# --------------------------------------------------------------------------

def test_relief_needs_a_plan_before_it_can_explain_one():
    """Same guard as rccp.run: every series zero means netreq has not run, not
    that the plant is comfortable."""
    from planbrain import rccp
    from planbrain.demo import build_demo, populate

    demo = build_demo(seed=7)
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((ROOT / "planbrain" / "facts" / "schema.sql").read_text(encoding="utf-8"))
    populate(con, demo)

    with pytest.raises(rccp.NoPlanError):
        rccp.relief(con, demo)


def test_relief_and_run_agree_on_which_buckets_are_overloaded():
    """Two entry points reading the same plan must not disagree about whether it
    fits -- and they apply capacity growth, so there are two chances to differ."""
    from planbrain import netreq, rccp
    from planbrain.demo import build_demo, populate
    from planbrain.facts.scenario import set_growth

    demo = build_demo(seed=7)
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((ROOT / "planbrain" / "facts" / "schema.sql").read_text(encoding="utf-8"))
    populate(con, demo)
    set_growth(con, scenario_id=0, capacity_growth_pct=10.0)
    netreq.run(con, demo, lot_sizing="cost_based")

    report = rccp.run(con, demo)
    explained = rccp.relief(con, demo)

    from_run = sum(len(d["overloaded_buckets"]) for d in report["resources"].values())
    assert explained["summary"]["buckets"] == from_run
