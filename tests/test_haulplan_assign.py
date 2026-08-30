"""Greedy largest-deficit assignment, and the feasibility filters that bind first.

The baseline any solver must beat. Simple on purpose: item 9 is about a ledger
that is right and explainable, not an optimiser.
"""

import pytest

from planbrain.haulplan import Ledger, Trip, Truck, jain_index, verdict
from planbrain.haulplan.assign import assign, feasible_trucks


def _fleet(n=3, capacity=16_000.0):
    return [Truck(truck_id=i, capacity_kg=capacity) for i in range(1, n + 1)]


def _long(trip_id, bucket, km=600.0, load=1000.0):
    return Trip(trip_id, bucket, km, load, is_long_haul=True)


# --------------------------------------------------------------------------
# feasibility binds before fairness
# --------------------------------------------------------------------------

def test_an_unavailable_truck_is_never_a_candidate():
    trucks = [Truck(1, 16_000.0, available=False), Truck(2, 16_000.0)]
    assert feasible_trucks(_long(900, 0), trucks, busy=set()) == [2]


def test_a_truck_too_small_for_the_load_is_never_a_candidate():
    trucks = [Truck(1, 9_000.0), Truck(2, 25_000.0)]
    assert feasible_trucks(_long(900, 0, load=14_000.0), trucks, busy=set()) == [2]


def test_a_truck_already_out_that_bucket_is_never_a_candidate():
    trucks = _fleet(2)
    assert feasible_trucks(_long(900, 0), trucks, busy={(1, 0)}) == [2]


def test_the_same_truck_is_free_again_in_a_later_bucket():
    trucks = _fleet(2)
    assert feasible_trucks(_long(900, 3), trucks, busy={(1, 0)}) == [1, 2]


def test_fairness_never_overrides_feasibility():
    """The truck furthest behind cannot carry the load, so it does not get it."""
    trucks = [Truck(1, 9_000.0), Truck(2, 25_000.0)]
    ledger = Ledger.opening({1: 0.0, 2: 50_000.0})

    plan = assign([_long(900, 0, load=20_000.0)], trucks, ledger)
    assert plan.assignments[0].truck_id == 2


def test_a_trip_no_truck_can_take_is_unassigned_not_forced():
    trucks = [Truck(1, 9_000.0)]
    ledger = Ledger.opening({1: 0.0})

    plan = assign([_long(900, 0, load=20_000.0)], trucks, ledger)
    assert plan.assignments == []
    assert len(plan.unassigned) == 1
    assert plan.unassigned[0].truck_id is None


def test_the_unassigned_reason_distinguishes_three_different_problems():
    """"No truck available" is useless to a planner. A fleet that is too small,
    fully committed, or not big enough are three different fixes."""
    ledger = Ledger.opening({1: 0.0})

    grounded = assign([_long(900, 0)], [Truck(1, 16_000.0, available=False)], ledger)
    assert "available" in grounded.unassigned[0].reason

    too_small = assign([_long(901, 0, load=20_000.0)], [Truck(1, 9_000.0)], ledger)
    assert "kg" in too_small.unassigned[0].reason

    committed = assign([_long(902, 0), _long(903, 0)], [Truck(1, 16_000.0)], ledger)
    assert "already committed" in committed.unassigned[0].reason


# --------------------------------------------------------------------------
# largest deficit
# --------------------------------------------------------------------------

def test_the_truck_furthest_behind_gets_the_long_run():
    trucks = _fleet(3)
    ledger = Ledger.opening({1: 40_000.0, 2: 12_000.0, 3: 30_000.0})

    plan = assign([_long(900, 0)], trucks, ledger)
    assert plan.assignments[0].truck_id == 2


def test_the_ledger_updates_within_the_run_so_trips_spread():
    """Without recording as it goes, every trip in the run would go to the same
    truck. This is the entire mechanism."""
    trucks = _fleet(3)
    ledger = Ledger.opening({1: 0.0, 2: 0.0, 3: 0.0})

    plan = assign([_long(900, 0), _long(901, 1), _long(902, 2)], trucks, ledger)
    assert sorted(a.truck_id for a in plan.assignments) == [1, 2, 3]


def test_short_haul_trips_do_not_shift_the_fairness_order():
    """Fairness is measured on long-haul only; a short run is not a favour."""
    trucks = _fleet(2)
    ledger = Ledger.opening({1: 0.0, 2: 0.0})

    assign([Trip(900, 0, 85.0, 500.0, is_long_haul=False)], trucks, ledger)
    assert ledger.long_haul_km(1) == 0.0
    assert ledger.total_km(1) == 85.0

    plan = assign([_long(901, 1)], trucks, ledger)
    assert plan.assignments[0].truck_id == 1


def test_assignment_is_deterministic():
    """A rerun must not reshuffle the fleet; drivers notice."""
    def run():
        return [
            a.truck_id
            for a in assign(
                [_long(900, 0), _long(901, 0), _long(902, 1)],
                _fleet(3),
                Ledger.opening({1: 0.0, 2: 0.0, 3: 0.0}),
            ).assignments
        ]

    assert run() == run()


def test_every_assignment_carries_a_reason_a_driver_would_accept():
    trucks = _fleet(2)
    ledger = Ledger.opening({1: 5_000.0, 2: 0.0})
    plan = assign([_long(900, 0)], trucks, ledger)
    assert "furthest behind" in plan.assignments[0].reason


# --------------------------------------------------------------------------
# does the greedy rule actually improve fairness?
# --------------------------------------------------------------------------

def test_long_haul_trips_get_first_claim_on_the_fair_trucks():
    """Regression for the ordering flaw the demo exposed.

    The first version processed trips by id, so short-haul runs were assigned
    first and consumed exactly the trucks furthest behind. By the time the
    long-haul trips came up, the only feasible trucks were the ones already
    ahead -- and fairness is measured on long-haul kilometres, so the index
    barely moved while every trip looked correctly assigned.

    Truck 1 is furthest behind and there is one short and one long trip in the
    bucket. Truck 1 must get the LONG one.
    """
    trucks = _fleet(2)
    ledger = Ledger.opening({1: 0.0, 2: 50_000.0})
    short = Trip(900, 0, 80.0, 1000.0, is_long_haul=False)
    long_run = Trip(901, 0, 600.0, 1000.0, is_long_haul=True)

    plan = assign([short, long_run], trucks, ledger)
    by_trip = {a.trip_id: a.truck_id for a in plan.assignments}
    assert by_trip[901] == 1, "the truck furthest behind must get the long haul"
    assert by_trip[900] == 2


def test_ordering_does_not_depend_on_the_order_trips_are_passed_in():
    """The rule is stated, not incidental to input order."""
    trucks = _fleet(2)
    short = Trip(900, 0, 80.0, 1000.0, is_long_haul=False)
    long_run = Trip(901, 0, 600.0, 1000.0, is_long_haul=True)

    def run(trips):
        ledger = Ledger.opening({1: 0.0, 2: 50_000.0})
        plan = assign(trips, trucks, ledger)
        return {a.trip_id: a.truck_id for a in plan.assignments}

    assert run([short, long_run]) == run([long_run, short])


def test_greedy_improves_fairness_on_a_skewed_opening_ledger():
    """The result the ledger exists to produce."""
    trucks = _fleet(4)
    opening = {1: 40_000.0, 2: 12_000.0, 3: 30_000.0, 4: 18_000.0}
    ledger = Ledger.opening(opening)

    before = jain_index(list(opening.values()))
    assign([_long(900 + i, i) for i in range(20)], trucks, ledger)
    after = jain_index(ledger.distribution())

    assert after > before
    assert verdict(after) in ("fair", "acceptable")


def test_a_fleet_that_starts_fair_stays_fair():
    trucks = _fleet(4)
    ledger = Ledger.opening({i: 20_000.0 for i in range(1, 5)})

    assign([_long(900 + i, i) for i in range(16)], trucks, ledger)
    assert jain_index(ledger.distribution()) == pytest.approx(1.0, abs=0.01)


def test_greedy_cannot_undo_a_gap_larger_than_the_work_available():
    """An honest limit. Four trips cannot level a 28,000 km deficit, and the
    report must not imply otherwise."""
    trucks = _fleet(2)
    ledger = Ledger.opening({1: 40_000.0, 2: 12_000.0})

    assign([_long(900 + i, i) for i in range(4)], trucks, ledger)
    after = jain_index(ledger.distribution())
    assert after < 0.95
    assert verdict(after) in ("acceptable", "unfair")
