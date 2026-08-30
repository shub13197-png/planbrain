"""The fairness ledger, and the zero-offset case tested before it was written.

Build item 9. The metric and its thresholds were committed in docs/haulplan.md
before any assignment ran.

The zero-offset tests below were written *first*, deliberately. The
zero-lead-time bug found during the reconciliation was exactly this class: an
order placed into a pipeline at bucket `t` after that bucket had been processed,
silently never arriving. Ten units ordered, zero delivered, no error anywhere.
Same-day dispatch is a real case for a fleet, so it gets the test before the code.
"""

import pytest

from planbrain.haulplan.fairness import (
    FAIR, ACCEPTABLE,
    coefficient_of_variation,
    jain_index,
    spread,
)
from planbrain.haulplan.ledger import Ledger, Trip, Truck


# --------------------------------------------------------------------------
# zero offset: written before the assignment code
# --------------------------------------------------------------------------

def test_a_same_day_trip_lands_in_the_ledger_for_that_bucket():
    """Zero offset between planning and dispatch. The trip must count now, not
    next bucket and not never."""
    ledger = Ledger.opening({1: 0.0})
    trip = Trip(trip_id=900, bucket=0, distance_km=600.0, load_kg=1000.0,
                is_long_haul=True)
    ledger.record(truck_id=1, trip=trip)

    assert ledger.long_haul_km(1) == 600.0
    assert ledger.flows[(1, 0)]["long_haul_km"] == 600.0


def test_a_same_day_trip_counts_toward_fairness_in_the_same_run():
    """The bug class: recorded somewhere but not visible to the decision that
    follows it in the same pass."""
    ledger = Ledger.opening({1: 0.0, 2: 0.0})
    ledger.record(truck_id=1, trip=Trip(900, 0, 600.0, 1000.0, True))

    assert ledger.deficit(2) > ledger.deficit(1)
    assert ledger.next_by_deficit([1, 2]) == 2


def test_several_same_day_trips_accumulate_within_one_bucket():
    ledger = Ledger.opening({1: 0.0})
    ledger.record(1, Trip(900, 0, 300.0, 500.0, True))
    ledger.record(1, Trip(901, 0, 200.0, 500.0, True))
    assert ledger.long_haul_km(1) == 500.0
    assert ledger.flows[(1, 0)]["trips_assigned"] == 2


def test_opening_carry_in_is_visible_immediately():
    """Year-to-date carry-in is a scalar position, and it must bind on the very
    first assignment of the run rather than after the first trip."""
    ledger = Ledger.opening({1: 40_000.0, 2: 12_000.0})
    assert ledger.next_by_deficit([1, 2]) == 2
    assert ledger.long_haul_km(1) == 40_000.0


# --------------------------------------------------------------------------
# the ledger's accounting
# --------------------------------------------------------------------------

def test_short_haul_kilometres_count_to_total_but_not_to_fairness():
    """Fairness is measured on long-haul; utilisation needs the total."""
    ledger = Ledger.opening({1: 0.0})
    ledger.record(1, Trip(900, 0, 85.0, 500.0, is_long_haul=False))

    assert ledger.long_haul_km(1) == 0.0
    assert ledger.total_km(1) == 85.0
    assert ledger.flows[(1, 0)]["trips_assigned"] == 1


def test_the_cumulative_ledger_is_derived_not_stored():
    """A stored cumulative would be a second meaning for a fact row, which
    docs/contracts/facts.md forbids permanently."""
    ledger = Ledger.opening({1: 1_000.0})
    ledger.record(1, Trip(900, 0, 400.0, 500.0, True))
    ledger.record(1, Trip(901, 3, 600.0, 500.0, True))

    assert ledger.long_haul_km(1) == 2_000.0
    assert set(ledger.flows) == {(1, 0), (1, 3)}
    assert all("cumulative" not in measures for measures in ledger.flows.values())


def test_flows_are_sparse():
    """Buckets with no trip produce no row at all."""
    ledger = Ledger.opening({1: 0.0, 2: 0.0})
    ledger.record(1, Trip(900, 5, 400.0, 500.0, True))
    assert list(ledger.flows) == [(1, 5)]


def test_deficit_is_measured_against_the_fleet_leader():
    ledger = Ledger.opening({1: 10_000.0, 2: 4_000.0, 3: 4_000.0})
    assert ledger.deficit(1) == 0.0
    assert ledger.deficit(2) == 6_000.0


def test_an_unknown_truck_is_refused_rather_than_defaulted():
    """A truck not in the opening ledger is a broken import, not a truck with
    zero kilometres. Defaulting would silently make it the fairest choice and
    it would take every long run."""
    ledger = Ledger.opening({1: 0.0})
    with pytest.raises(KeyError, match="99"):
        ledger.record(99, Trip(900, 0, 400.0, 500.0, True))
    with pytest.raises(KeyError, match="99"):
        ledger.long_haul_km(99)


# --------------------------------------------------------------------------
# the fairness metric, per docs/haulplan.md
# --------------------------------------------------------------------------

def test_a_perfectly_equal_fleet_scores_one():
    assert jain_index([100.0, 100.0, 100.0]) == pytest.approx(1.0)


def test_one_truck_taking_everything_scores_one_over_n():
    """The floor of the index, and the failure mode being modelled."""
    assert jain_index([300.0, 0.0, 0.0]) == pytest.approx(1 / 3)


def test_the_index_reads_as_an_equivalent_equal_split():
    """J = 0.8 on ten trucks means as fair as an equal split among eight.

    This is the property that makes the number actionable, and the reason it was
    chosen over Gini.
    """
    values = [1.0] * 8 + [0.0] * 2
    assert jain_index(values) == pytest.approx(0.8)


def test_the_index_is_scale_invariant():
    """A 12-truck depot and a 40-truck depot must be directly comparable."""
    base = [10.0, 20.0, 30.0]
    assert jain_index(base) == pytest.approx(jain_index([v * 1000 for v in base]))


def test_an_empty_or_all_zero_fleet_has_no_fairness_to_report():
    """Not 1.0. A fleet that has driven nothing is not perfectly fair, it is
    unmeasured, and reporting perfection would flatter every empty run."""
    assert jain_index([]) is None
    assert jain_index([0.0, 0.0]) is None


def test_the_committed_thresholds_are_the_ones_in_the_docs():
    assert FAIR == 0.95
    assert ACCEPTABLE == 0.85


def test_the_ceiling_is_reached_when_there_is_enough_work():
    """Enough kilometres to level the fleet entirely means a ceiling of 1.0."""
    from planbrain.haulplan import ceiling

    assert ceiling([100.0, 0.0], work_available=100.0) == pytest.approx(1.0)


def test_the_ceiling_is_bounded_by_the_work_available():
    """The finding this exists to make measurable: a gap larger than all the
    work there is cannot be closed, however cleverly the work is assigned."""
    from planbrain.haulplan import ceiling

    limited = ceiling([100.0, 0.0], work_available=10.0)
    assert limited is not None
    assert limited < 1.0
    assert limited > jain_index([100.0, 0.0])


def test_the_ceiling_never_falls_below_the_opening():
    """Adding work to the trucks furthest behind cannot make a fleet less fair."""
    from planbrain.haulplan import ceiling

    opening = [40_000.0, 12_000.0, 30_000.0]
    assert ceiling(opening, 5_000.0) >= jain_index(opening)


def test_spread_and_variation_are_reported_alongside():
    """Jain alone can hide a large absolute gap on a big fleet."""
    values = [40_000.0, 12_000.0]
    assert spread(values) == 28_000.0
    assert coefficient_of_variation(values) > 0
