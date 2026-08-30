"""Reconciling netreq's planned stock with the simulation's replayed stock.

The assertion here is NOT that the two engines agree. It is that every part of
the difference has a name and the names add up. A test demanding agreement would
be wrong, and passing it would mean one engine had been bent to fit the other.
"""

import dataclasses

import pytest

from planbrain import reconcile
from planbrain.demo import build_demo, populate
from planbrain.forecast import demand_keys
from planbrain.reconcile import RESIDUAL_TOLERANCE, TERMS, replay_schedule, total_change
from planbrain.simulate.core import replay

SAMPLE = 24


@pytest.fixture(scope="module")
def demo():
    return build_demo(seed=7)


@pytest.fixture(scope="module")
def sample_keys(demo):
    keys = demand_keys(demo)
    step = len(keys) / SAMPLE
    return [keys[int(i * step)] for i in range(SAMPLE)]


@pytest.fixture(scope="module")
def report(demo, sample_keys):
    import sqlite3
    from pathlib import Path

    schema = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(schema.read_text(encoding="utf-8"))
    populate(con, demo)
    result = reconcile.reconcile(con, demo, keys=sample_keys, holdout_days=90)
    con.close()
    return result


# --------------------------------------------------------------------------
# the fixed-schedule replay
# --------------------------------------------------------------------------

def test_untruncated_balance_may_go_negative():
    """Rung 3 must allow negative stock, or truncation cannot be isolated."""
    trace = replay_schedule([0.0, 0.0], [10.0, 10.0], opening=5.0, truncate=False)
    assert trace == [-5.0, -15.0]


def test_truncation_floors_the_balance_at_zero():
    trace = replay_schedule([0.0, 0.0], [10.0, 10.0], opening=5.0, truncate=True)
    assert trace == [0.0, 0.0]


def test_receipts_are_available_in_the_bucket_they_land():
    trace = replay_schedule([10.0, 0.0], [10.0, 0.0], opening=0.0, truncate=True)
    assert trace == [0.0, 0.0]


def test_the_two_replay_implementations_agree():
    """The genuinely non-trivial check in this file.

    ``replay_schedule`` and ``simulate.replay`` are separate implementations of
    the same physics. Feeding the simulation a fixed schedule at zero lead time
    must reproduce the ladder's truncated rung. This is what caught the
    zero-lead-time bug in simulate.replay, where an order placed into the
    pipeline at bucket t was never collected because that bucket's arrivals had
    already been popped.
    """
    receipts = [50.0, 0.0, 0.0, 80.0, 0.0, 0.0]
    demand = [20.0, 20.0, 20.0, 20.0, 20.0, 20.0]

    ladder = replay_schedule(receipts, demand, opening=10.0, truncate=True)
    simulated = replay(
        demand,
        lambda t, on_hand, inbound: receipts[t],
        initial_on_hand=10.0,
        lead_time_days=0,
    )
    assert simulated.average_on_hand == pytest.approx(sum(ladder) / len(ladder))


def test_a_zero_lead_time_order_actually_arrives():
    """Regression for the bug the cross-check found. It failed silently: ten
    units ordered, zero received, no error anywhere."""
    out = replay(
        [10.0], lambda t, on_hand, inbound: 10.0 if t == 0 else 0.0,
        initial_on_hand=0.0, lead_time_days=0,
    )
    assert out.units_served == 10.0
    assert out.fill_rate == 1.0


# --------------------------------------------------------------------------
# the ladder
# --------------------------------------------------------------------------

def test_every_rung_and_term_is_present(report):
    assert report.evaluated > 0
    for ladder in report.ladders:
        assert set(ladder.terms) == set(TERMS)
        assert len(ladder.rungs) == 5


def test_terms_sum_to_the_total_change_per_series(report):
    """By construction, and asserted anyway as a guard against a coding slip."""
    for ladder in report.ladders:
        assert sum(ladder.terms.values()) == pytest.approx(total_change(ladder))


# --------------------------------------------------------------------------
# falsification: which of these checks can actually fail?
# --------------------------------------------------------------------------

def test_the_term_sum_is_an_identity_and_cannot_fail():
    """Demonstrates that the decomposition sum is NOT evidence.

    Each term is a difference between adjacent rungs, so they collapse to the
    observed gap whatever the rungs contain. Fed pure noise, the check still
    passes. It is a guard against coding slips and nothing more, and reporting
    it as verification would overstate what is known.
    """
    import random

    rng = random.Random(1)
    for _ in range(20):
        r0, r1, r2, r3, r4 = (rng.uniform(-1000, 1000) for _ in range(5))
        terms = {
            "safety_stock": r1 - r0,
            "lot_granularity": r2 - r1,
            "forecast_error": r3 - r2,
            "stockout_truncation": r4 - r3,
        }
        observed = r2 - r4
        explained = -(terms["forecast_error"] + terms["stockout_truncation"])
        assert observed - explained == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("injected", [0.02, 0.10, 0.40])
def test_the_cross_check_residual_moves_with_an_injected_error(injected):
    """The falsification the term sum cannot provide.

    Corrupt the schedule handed to one engine and the residual must move, and
    move roughly in proportion. If it did not, the two sides would not be
    independent and the check would be measuring itself.
    """
    receipts = [120.0, 0.0, 0.0, 90.0, 0.0, 60.0, 0.0, 0.0]
    demand = [20.0] * 8
    opening = 30.0

    clean = _residual(receipts, receipts, demand, opening)
    assert clean == pytest.approx(0.0, abs=1e-9)

    corrupted = [r * (1 + injected) for r in receipts]
    moved = _residual(receipts, corrupted, demand, opening)
    assert abs(moved) > abs(clean) + 1e-6, "an injected error must be visible"

    # Proportional: doubling the injection roughly doubles the residual.
    doubled = _residual(receipts, [r * (1 + injected * 2) for r in receipts],
                        demand, opening)
    assert abs(doubled) == pytest.approx(abs(moved) * 2, rel=0.15)


def test_an_off_by_one_schedule_shift_is_caught():
    """The error class the ladder exists to guard against: a series shifted by
    one bucket looks entirely plausible and is entirely wrong."""
    receipts = [120.0, 0.0, 0.0, 90.0, 0.0, 60.0, 0.0, 0.0]
    shifted = [0.0] + receipts[:-1]
    demand = [20.0] * 8

    residual = _residual(receipts, shifted, demand, opening=30.0)
    assert abs(residual) > 1.0


def _residual(ladder_receipts, simulated_receipts, demand, opening):
    """Ladder rung 4 against the simulation, with independently supplied schedules."""
    from planbrain.reconcile import simulate_schedule

    trace = replay_schedule(ladder_receipts, demand, opening=opening, truncate=True)
    ladder_mean = sum(trace) / len(trace)
    return ladder_mean - simulate_schedule(simulated_receipts, demand, opening=opening)


def test_the_named_terms_account_for_the_observed_gap(report):
    """The construction guard. Passes by algebra; see the identity test above.

    Kept because it would catch a coding slip in how the terms are assembled,
    but it is not evidence and the reported residual is no longer based on it.
    """
    assert report.construction_check == pytest.approx(0.0, abs=1e-6)


def test_the_reported_residual_is_the_falsifiable_one(report):
    """The headline assertion, and the reason this item exists.

    Not that the two engines agree on stock -- they should not. That the ladder
    and the service simulation, two separately written implementations of the
    same physics, produce the same rung 4 for the same schedule.
    """
    assert report.residual_share <= RESIDUAL_TOLERANCE
    assert report.within_tolerance
    for ladder in report.ladders:
        assert ladder.cross_check_residual == pytest.approx(0.0, abs=1e-6)


def test_a_residual_would_be_reported_not_hidden(report):
    """The tolerance exists for float noise over 90 buckets, not to absorb
    unexplained difference. If it is ever breached, this must be visible."""
    assert RESIDUAL_TOLERANCE <= 0.01
    # Behaviour, not type: a residual outside tolerance must flip the verdict,
    # so a breach cannot pass unnoticed.
    breached = dataclasses.replace(report, residual_share=RESIDUAL_TOLERANCE * 2)
    assert not breached.within_tolerance


def test_safety_stock_and_lot_sizing_explain_the_plans_own_stock(report):
    """The other half of the decomposition: not the gap, but the plan itself.

    Pure netting under lot-for-lot with no safety stock carries almost nothing,
    so essentially all of netreq's planned inventory is one of these two
    deliberate choices rather than an artefact.
    """
    pure = sum(l.rungs["pure_netting"] for l in report.ladders) / report.evaluated
    plan = report.plan_on_hand.value
    assert abs(pure) < abs(plan) * 0.1

    deliberate = report.terms["safety_stock"] + report.terms["lot_granularity"]
    assert deliberate == pytest.approx(plan - pure, rel=1e-6)


def test_results_are_reported_per_demand_class(report):
    """Aggregate reconciliation can hide a term that cancels between classes."""
    assert report.by_pattern
    for block in report.by_pattern.values():
        assert block["n"] > 0
        for term in TERMS:
            assert term in block


def test_stock_figures_carry_their_denominators(report):
    from planbrain.forecast.metrics import ScoredMean

    assert isinstance(report.plan_on_hand, ScoredMean)
    assert isinstance(report.replayed_on_hand, ScoredMean)
    with pytest.raises(TypeError):
        float(report.plan_on_hand)


# --------------------------------------------------------------------------
# unit costs
# --------------------------------------------------------------------------

def test_costs_roll_up_through_the_bill_of_materials(demo):
    """A blend costs at least the sum of what goes into it."""
    cost = {p.sku_id: p.unit_cost for p in demo.parts}
    children = {}
    for edge in demo.bom:
        children.setdefault(edge.parent_sku_id, []).append(edge)

    for part in demo.parts:
        if part.level == "raw":
            continue
        rolled = sum(
            cost[edge.child_sku_id] * edge.qty_per
            for edge in children.get(part.sku_id, ())
        )
        assert part.unit_cost >= rolled - 1e-6


def test_every_part_has_a_positive_cost(demo):
    assert all(p.unit_cost > 0 for p in demo.parts)


def test_additives_cost_more_than_base_oils(demo):
    """The prices come from type, per docs/unit-costs.md, not from a flat number."""
    from planbrain.demo.generate import ADDITIVE_COST, BASE_OIL_COST

    raws = {p.unit_cost for p in demo.parts if p.level == "raw"}
    assert raws == {BASE_OIL_COST, ADDITIVE_COST}
    assert ADDITIVE_COST > BASE_OIL_COST


def test_cost_based_lot_sizing_uses_money_not_hours(demo):
    """The item 7 finding: pricing holding by embedded machine-hours made
    holding nearly free and produced quarter-long campaigns."""
    from planbrain.netreq import cost_lot_sizing
    from planbrain.netreq.explode import ANNUAL_CARRYING_RATE, CAPACITY_COST_PER_HOUR

    sizing = cost_lot_sizing(demo.routings, demo.parts)
    assert sizing

    routing = next(r for r in demo.routings if r.sku_id in sizing)
    part = next(p for p in demo.parts if p.sku_id == routing.sku_id)
    ls = sizing[routing.sku_id]
    assert ls.setup_cost == pytest.approx(routing.setup_hours * CAPACITY_COST_PER_HOUR)
    assert ls.holding_cost == pytest.approx(part.unit_cost * ANNUAL_CARRYING_RATE / 365)


def test_the_carrying_rate_was_not_tuned(demo):
    """Explicitly out of bounds in docs/unit-costs.md. Adjusting it to fix
    campaign length would be fitting a parameter to a desired answer."""
    from planbrain.netreq.explode import ANNUAL_CARRYING_RATE

    assert ANNUAL_CARRYING_RATE == 0.25
