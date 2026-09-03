"""Inventory reported in money, and the ways that number could lie.

Units alone cannot be compared across a portfolio -- a thousand fasteners and a
thousand castings are not the same decision -- so working capital is the term a
planner is actually answerable for. It is also a total rather than a mean, which
makes it easy to quote without the denominator that gives it meaning.
"""

import sqlite3
from pathlib import Path

import pytest

from planbrain.simulate import InventoryValue, _inventory_value

ROOT = Path(__file__).resolve().parents[1]


class _Outcome:
    def __init__(self, average_on_hand):
        self.average_on_hand = average_on_hand


def test_an_unpriced_part_is_counted_rather_than_treated_as_free():
    """The failure this guards is silent and flattering: a part with no unit
    cost contributes nothing, so a half-priced portfolio reports half the
    working capital and looks better than it is."""
    runs = {("A", "PLANT"): _Outcome(100.0), ("B", "PLANT"): _Outcome(100.0)}
    value = _inventory_value(runs, {"A": 5.0})

    assert value.total == 500.0
    assert value.series == 2
    assert value.unpriced == 1
    assert value.complete is False, (
        "a value covering half the series must not report itself as complete"
    )


def test_a_zero_cost_counts_as_unpriced_not_as_a_free_part():
    """0.0 is what a missing cost looks like after it has been through a
    spreadsheet. Treating it as a genuine price is how the total goes quietly
    wrong."""
    runs = {("A", "PLANT"): _Outcome(100.0)}
    assert _inventory_value(runs, {"A": 0.0}).unpriced == 1


def test_the_value_is_a_total_and_scales_with_the_series_it_covers():
    """If it behaved like a mean, adding series would not move it, and the
    number would be comparable across samples in a way it is not."""
    one = _inventory_value({("A", "P"): _Outcome(10.0)}, {"A": 3.0})
    two = _inventory_value(
        {("A", "P"): _Outcome(10.0), ("B", "P"): _Outcome(10.0)},
        {"A": 3.0, "B": 3.0},
    )
    assert two.total == 2 * one.total
    assert (one.series, two.series) == (1, 2)


def test_it_cannot_be_quoted_without_its_denominator():
    """Same rule as ScoredMean: no __float__, so a total cannot be dropped into
    a format string as though it stood on its own."""
    value = _inventory_value({("A", "P"): _Outcome(10.0)}, {"A": 3.0})
    with pytest.raises(TypeError):
        float(value)


def test_the_carrying_rate_is_the_same_one_lot_sizing_uses():
    """Two numbers in one product describing the cost of holding stock must not
    disagree. Cost-based lot sizing prices changeover against carrying at
    ANNUAL_CARRYING_RATE; if this used a different rate, a plan could be
    optimised against one figure and reported against another."""
    from planbrain.netreq.explode import ANNUAL_CARRYING_RATE

    value = _inventory_value({("A", "P"): _Outcome(100.0)}, {"A": 10.0})
    assert value.carrying_rate == ANNUAL_CARRYING_RATE
    assert value.annual_carrying == pytest.approx(value.total * ANNUAL_CARRYING_RATE)


def test_an_empty_run_reports_nothing_rather_than_a_confident_zero():
    """Zero series and zero value are different from zero value across the
    portfolio, and the series count is what distinguishes them."""
    value = _inventory_value({}, {})
    assert value.total == 0.0
    assert value.series == 0


# --------------------------------------------------------------------------
# end to end, against the demo
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def compared():
    from planbrain import simulate
    from planbrain.demo import build_demo, populate
    from planbrain.forecast import demand_keys

    demo = build_demo(seed=7)
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((ROOT / "planbrain" / "facts" / "schema.sql").read_text(encoding="utf-8"))
    populate(con, demo)
    keys = demand_keys(demo)[:24]
    return simulate.compare(con, demo, keys=keys, safety_days=7.0)


def test_every_policy_reports_what_its_inventory_is_worth(compared):
    for name, policy in compared["policies"].items():
        value = policy.inventory_value
        assert isinstance(value, InventoryValue), name
        assert value.series > 0
        assert value.complete, f"{name} has unpriced parts in the demo, which is seeded"


def test_holding_more_stock_costs_more_money(compared):
    """The direction that must hold, whatever the demo happens to generate.

    Asserted as an ordering rather than as a figure: the point is that the two
    measures cannot disagree, not that either has a particular value.
    """
    policies = compared["policies"]
    by_units = sorted(policies, key=lambda n: policies[n].average_on_hand.value)
    by_money = sorted(policies, key=lambda n: policies[n].inventory_value.total)
    assert by_units[0] == by_money[0], (
        "the policy holding the fewest units is not the cheapest, which means "
        "units and money are being computed from different runs"
    )


def test_the_service_and_capital_comparison_is_available_together(compared):
    """A fill rate without its inventory cost is half an answer, and it is the
    half that flatters. Both must come out of one call."""
    for policy in compared["policies"].values():
        assert policy.fill_rate is not None
        assert policy.inventory_value is not None
