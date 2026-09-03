"""Safety stock as a service target, and the gap between what it asks for and
what it delivers.

The headline finding is in `docs/service-backtest.md`: a requested **cycle
service level** is not a fill rate, and on this portfolio the two are far apart.
These tests pin the arithmetic and the honesty, not the gap itself -- the gap is
a property of the data and belongs in the register.
"""

import math
import sqlite3
from pathlib import Path
from statistics import NormalDist

import pytest

from planbrain.simulate.policies import safety_stock_for_service

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# the formula
# --------------------------------------------------------------------------

def test_it_is_the_textbook_periodic_review_form():
    """SS = z * sigma * sqrt(L + R). Silver, Pyke & Peterson ch. 7."""
    z = NormalDist().inv_cdf(0.95)
    expected = z * 12.0 * math.sqrt(7 + 1)
    assert safety_stock_for_service(12.0, lead_time_days=7, service_level=0.95) == \
        pytest.approx(expected)


def test_a_half_service_level_asks_for_no_safety_stock():
    """z(0.5) is zero, and "I will be short half the time" is exactly what no
    safety stock means. A formula that returned something here would be adding
    stock nobody asked for."""
    assert safety_stock_for_service(50.0, lead_time_days=10, service_level=0.5) == 0.0


def test_safety_stock_rises_with_the_service_level_and_with_lead_time():
    """Both directions, because either could be inverted without the other
    noticing."""
    base = safety_stock_for_service(10.0, lead_time_days=7, service_level=0.90)
    assert safety_stock_for_service(10.0, lead_time_days=7, service_level=0.99) > base
    assert safety_stock_for_service(10.0, lead_time_days=28, service_level=0.90) > base


def test_a_steady_series_needs_no_safety_stock_whatever_the_service_level():
    """Zero variance means the lead-time demand is known exactly. Any positive
    answer here would be stock held against uncertainty that does not exist."""
    assert safety_stock_for_service(0.0, lead_time_days=14, service_level=0.99) == 0.0


@pytest.mark.parametrize("level", [0.0, 1.0, -0.2, 1.4, 95])
def test_a_service_level_that_is_not_a_probability_is_refused(level):
    """1.0 demands infinite stock and 95 is someone typing a percentage. Both
    are refused rather than clamped: clamping 95 to 0.95 would guess, and
    guessing is how a plan acquires a number nobody chose."""
    with pytest.raises(ValueError, match="probability"):
        safety_stock_for_service(10.0, lead_time_days=7, service_level=level)


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def demo():
    from planbrain.demo import build_demo

    return build_demo(seed=7)


def _fresh(demo):
    from planbrain.demo import populate

    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((ROOT / "planbrain" / "facts" / "schema.sql").read_text(encoding="utf-8"))
    populate(con, demo)
    return con


def test_the_two_safety_rules_cannot_be_combined(demo):
    """Days of cover and a service level are two rules for one quantity.
    Silently preferring either would make the safety stock depend on an argument
    order nobody can see."""
    from planbrain import simulate
    from planbrain.forecast import demand_keys

    con = _fresh(demo)
    with pytest.raises(ValueError, match="not both"):
        simulate.compare(con, demo, keys=demand_keys(demo)[:4],
                         safety_days=7.0, safety_service_level=0.95)


def test_the_report_states_which_rule_produced_it(demo):
    """A service figure without its safety rule cannot be compared with another
    one, and both rules produce plausible numbers."""
    from planbrain import simulate
    from planbrain.forecast import demand_keys

    keys = demand_keys(demo)[:8]
    days = simulate.compare(_fresh(demo), demo, keys=keys, safety_days=7.0)
    level = simulate.compare(_fresh(demo), demo, keys=keys, safety_service_level=0.95)

    assert days["safety_rule"] == "7 days of cover"
    assert level["safety_rule"] == "95% cycle service level"


def test_the_service_level_reaches_the_reorder_point_as_well_as_the_forecast(demo):
    """The reorder point takes a multiplier on sigma rather than an absolute
    quantity. If the service level only reached the forecast policy, the
    comparison would silently be between two different rules."""
    from planbrain import simulate
    from planbrain.forecast import demand_keys

    keys = demand_keys(demo)[:16]
    low = simulate.compare(_fresh(demo), demo, keys=keys, safety_service_level=0.55)
    high = simulate.compare(_fresh(demo), demo, keys=keys, safety_service_level=0.99)

    for policy in ("forecast", "reorder_point"):
        assert (high["policies"][policy].inventory_value.total
                > low["policies"][policy].inventory_value.total), (
            f"{policy} held no more stock at 99% than at 55%, so the service "
            f"level is not reaching it"
        )


def test_a_higher_service_level_never_lowers_the_fill_rate(demo):
    """The monotonicity that must hold whatever the demand looks like. The gap
    between requested and achieved is a property of the data and lives in the
    register; the direction is a property of the code and lives here."""
    from planbrain import simulate
    from planbrain.forecast import demand_keys

    keys = demand_keys(demo)[:24]
    fills = [
        simulate.compare(_fresh(demo), demo, keys=keys, safety_service_level=level)
        ["policies"]["forecast"].fill_rate.value
        for level in (0.80, 0.95, 0.99)
    ]
    assert fills == sorted(fills), f"fill rate fell as service level rose: {fills}"
