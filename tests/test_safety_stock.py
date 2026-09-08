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

from planbrain.simulate.policies import (
    achieved_fill_rate,
    level_by_simulation,
    level_for_exceedance,
    order_up_to_for_fill_rate,
    safety_stock_for_service,
)

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


# --------------------------------------------------------------------------
# asking for a fill rate and getting one
# --------------------------------------------------------------------------

#: Nine orders of 100 in ninety days: the canonical intermittent series, and
#: 93-96% of both real datasets is this shape.
INTERMITTENT = [100.0 if i % 10 == 0 else 0.0 for i in range(90)]


@pytest.mark.parametrize("target", [0.50, 0.75, 0.90, 0.95, 0.99])
def test_a_requested_fill_rate_is_actually_delivered(target):
    """The point of the rule: ask for a fill rate, get that fill rate.

    `safety_stock_for_service` cannot do this and is not being blamed for it --
    it targets a *cycle service level*, which is a different quantity. What it
    cannot do is be used as though it targeted fill rate, which is what the
    benchmark scores.
    """
    level = order_up_to_for_fill_rate(INTERMITTENT, lead_time_days=6, fill_rate=target)
    assert achieved_fill_rate(INTERMITTENT, level, lead_time_days=6) == pytest.approx(
        target, abs=0.01
    )


def test_the_normal_approximation_misses_on_the_same_series():
    """Why the rule above had to exist, asserted rather than asserted-about.

    On intermittent demand the normal approximation under-delivers badly at low
    targets and buys nothing at high ones. At 50% it asks for no stock at all
    and serves none of the demand; at 99% it holds more than the largest window
    of demand that has ever occurred, so the last third of that stock cannot
    change the outcome.
    """
    sd = math.sqrt(sum(x * x for x in INTERMITTENT) / len(INTERMITTENT)
                   - (sum(INTERMITTENT) / len(INTERMITTENT)) ** 2)

    low = safety_stock_for_service(sd, lead_time_days=6, service_level=0.50)
    assert achieved_fill_rate(INTERMITTENT, low, lead_time_days=6) < 0.01, (
        "a 50% cycle service level happens to deliver a usable fill rate here, "
        "which would remove the reason this rule exists"
    )

    high = safety_stock_for_service(sd, lead_time_days=6, service_level=0.99)
    assert high > max(INTERMITTENT) * 1.5, (
        "the normal approximation no longer overshoots the largest possible "
        "requirement, so the waste this rule avoids is gone"
    )


def test_more_fill_rate_never_asks_for_less_stock():
    levels = [order_up_to_for_fill_rate(INTERMITTENT, lead_time_days=6, fill_rate=f)
              for f in (0.5, 0.75, 0.9, 0.99)]
    assert levels == sorted(levels)


def test_a_sku_nobody_ordered_needs_no_stock():
    """Stock held against a number rather than a customer."""
    assert order_up_to_for_fill_rate([0.0] * 30, lead_time_days=6, fill_rate=0.95) == 0.0


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5])
def test_a_fill_rate_that_is_not_a_fraction_is_refused(bad):
    with pytest.raises(ValueError, match="strictly between 0 and 1"):
        order_up_to_for_fill_rate(INTERMITTENT, lead_time_days=6, fill_rate=bad)


# --------------------------------------------------------------------------
# allocating a stock budget across parts, rather than per part
# --------------------------------------------------------------------------

#: Two parts with similar average demand and very different shapes: one that
#: varies mildly every day, one that is quiet then large. Both need enough
#: distinct protection-window totals for a budget to be split between them --
#: a two-valued series can only be stocked all or nothing, which cannot show
#: an allocation argument either way.
STEADY = [8.0 + (i * 7 % 5) for i in range(240)]
SPIKY = [0.0] * 240
for _i in range(0, 240, 9):
    SPIKY[_i] = 40.0 + (_i * 13 % 60)


def test_the_level_falls_as_the_exceedance_target_tightens():
    levels = [level_for_exceedance(SPIKY, lead_time_days=6, exceedance=e)
              for e in (0.5, 0.25, 0.1, 0.01)]
    assert levels == sorted(levels)


def test_it_equalises_the_chance_of_running_out_not_the_service_level():
    """The whole point, and the thing that makes it a portfolio rule.

    At one common exceedance target the two parts get levels whose *probability
    of being exceeded* matches, which is the condition for the last unit of
    stock being worth the same wherever it is spent. Equal fill rate does not
    have that property, so it leaves service on the table.
    """
    theta = 0.10
    for series in (STEADY, SPIKY):
        level = level_for_exceedance(series, lead_time_days=6, exceedance=theta)
        window = [sum(series[i:i + 7]) for i in range(len(series) - 6)]
        over = sum(1 for w in window if w > level) / len(window)
        assert over <= theta + 1e-9, f"{over} exceeds the target {theta}"


def test_it_serves_more_than_equal_fill_rate_for_the_same_stock():
    """Falsifiable head to head. Same two parts, same total stock, two ways of
    splitting it -- the allocation rule must serve at least as many units."""
    def served(levels):
        total = 0.0
        for series, level in zip((STEADY, SPIKY), levels):
            window = [sum(series[i:i + 7]) for i in range(len(series) - 6)]
            total += sum(min(w, level) for w in window)
        return total

    equal_fill = [order_up_to_for_fill_rate(s, lead_time_days=6, fill_rate=0.9)
                  for s in (STEADY, SPIKY)]
    budget = sum(equal_fill)

    # The exceedance target that spends the same budget.
    lo, hi = 0.0, 1.0
    for _ in range(50):
        mid = (lo + hi) / 2
        levels = [level_for_exceedance(s, lead_time_days=6, exceedance=mid)
                  for s in (STEADY, SPIKY)]
        if sum(levels) > budget:
            lo = mid
        else:
            hi = mid
    allocated = [level_for_exceedance(s, lead_time_days=6, exceedance=hi)
                 for s in (STEADY, SPIKY)]

    assert sum(allocated) <= budget + 1e-6, "the comparison must spend no more"
    assert served(allocated) >= served(equal_fill) - 1e-6, (
        "equalising the chance of running out served fewer units than equalising "
        "the service level, for the same stock -- the allocation argument fails"
    )


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_an_exceedance_that_is_not_a_probability_is_refused(bad):
    with pytest.raises(ValueError, match="between 0 and 1"):
        level_for_exceedance(SPIKY, lead_time_days=6, exceedance=bad)


# --------------------------------------------------------------------------
# choosing the level by simulating it, rather than deriving it
# --------------------------------------------------------------------------

def test_it_finds_the_smallest_level_that_reaches_the_target():
    """The level is read off a simulation of the policy, not off a formula.

    Every analytic rule here fits a distribution to the *whole* training window.
    A manufacturer with six years of history is then stocked for regimes that
    ended -- the level implied by its recent year is 13% lower at the median --
    and a twelve-week moving average in a spreadsheet beats it on share of
    demand served for exactly that reason. Simulating on a recent window
    sidesteps the fitting question: what matters is what the policy would have
    done lately.
    """
    evaluate = lambda level: min(1.0, level / 200.0)
    got = level_by_simulation(evaluate, target=0.9, hi=400.0)
    assert got == pytest.approx(180.0, abs=1.0)
    assert evaluate(got) >= 0.9


def test_an_unreachable_target_spends_the_budget_rather_than_pretending():
    """No level in range reaches it, so the honest answer is the most stock the
    caller allowed -- not a number that looks like a solution."""
    assert level_by_simulation(lambda level: 0.4, target=0.9, hi=400.0) == 400.0


def test_a_window_with_no_demand_asks_for_no_stock():
    """`Outcome.fill_rate` is None when nothing was demanded. Reading that as
    success would stock a part nobody ordered; reading it as failure would stock
    it to the ceiling."""
    assert level_by_simulation(lambda level: None, target=0.9, hi=400.0) == 0.0


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.2, 1.4])
def test_a_simulated_target_that_is_not_a_fraction_is_refused(bad):
    with pytest.raises(ValueError, match="strictly between 0 and 1"):
        level_by_simulation(lambda level: 0.5, target=bad, hi=400.0)


def test_more_simulated_service_never_asks_for_less_stock():
    evaluate = lambda level: min(1.0, level / 200.0)
    levels = [level_by_simulation(evaluate, target=t, hi=400.0)
              for t in (0.5, 0.75, 0.9, 0.99)]
    assert levels == sorted(levels)
