"""Growth assumptions: the arithmetic, and the promise that zero costs nothing.

The rules these assert were committed in `docs/forecast.md` before any of the
code existed, because a growth overlay is the easiest way in this codebase to
produce a number that is wrong and looks right.

The first test is the kill condition, written before the feature: a planner who
has not opted into an assumption must not be silently given one.
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from planbrain.forecast.growth import growth_factors

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# the arithmetic, with no database in the way
# --------------------------------------------------------------------------

def _spine(start: date, days: int):
    return [start + timedelta(days=i) for i in range(days)]


def test_zero_growth_is_exactly_one_at_every_bucket():
    """Not approximately one. The committed kill condition is that growth of
    zero produces identical numbers, and `1.0000000001 * qty` is not identical."""
    anchor = date(2026, 6, 30)
    factors = growth_factors(_spine(date(2026, 7, 1), 90), anchor=anchor, annual_pct=0.0)
    assert factors == [1.0] * 90


def test_growth_is_anchored_at_the_last_actual_not_the_first_planned_bucket():
    """The rule most likely to be "simplified" later.

    The horizon starts after the last actual. Anchoring at `horizon_start` would
    make the first planned bucket carry no growth at all, despite being a month
    past the level the history establishes -- an error that is small, always in
    the same direction, and invisible in any output.
    """
    anchor = date(2026, 6, 30)
    spine = _spine(date(2026, 7, 30), 10)          # a 30-day gap
    factors = growth_factors(spine, anchor=anchor, annual_pct=12.0)

    assert factors[0] == pytest.approx(1.12 ** (30 / 365))
    assert factors[0] > 1.009, (
        "the first planned bucket carries no growth, so the overlay is anchored "
        "at the horizon rather than at the last actual"
    )


def test_growth_compounds_daily_and_reaches_the_annual_rate_after_a_year():
    anchor = date(2026, 6, 30)
    spine = [anchor + timedelta(days=365)]
    assert growth_factors(spine, anchor=anchor, annual_pct=8.0)[0] == pytest.approx(1.08)


def test_decline_is_representable():
    """A shrinking business is a plan case, not an error. A parameter that only
    accepts growth quietly tells the user their situation is not supported."""
    anchor = date(2026, 6, 30)
    factors = growth_factors([anchor + timedelta(days=365)], anchor=anchor, annual_pct=-10.0)
    assert factors[0] == pytest.approx(0.90)


def test_a_rate_below_minus_one_hundred_is_refused():
    """(1 + g) negative would alternate sign with the exponent and produce
    complex or oscillating demand. Refused at the edge rather than clamped, so
    the caller learns the input was nonsense."""
    with pytest.raises(ValueError, match="-100"):
        growth_factors([date(2026, 7, 1)], anchor=date(2026, 6, 30), annual_pct=-140.0)


def test_buckets_before_the_anchor_shrink_rather_than_being_clamped():
    """A backtest window sits before `history_end`. Clamping those to 1.0 would
    make the overlay a step function at the anchor and put a discontinuity in
    the middle of any evaluation that spans it."""
    anchor = date(2026, 6, 30)
    factors = growth_factors([anchor - timedelta(days=365)], anchor=anchor, annual_pct=10.0)
    assert factors[0] == pytest.approx(1 / 1.10)


# --------------------------------------------------------------------------
# the kill condition, end to end
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def demo():
    from planbrain.demo import build_demo

    return build_demo(seed=7)


def _fresh(demo):
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((ROOT / "planbrain" / "facts" / "schema.sql").read_text(encoding="utf-8"))
    from planbrain.demo import populate

    populate(con, demo)
    return con


def _forecast_rows(con, demo, keys):
    from planbrain import forecast
    from planbrain.facts.access import read_facts

    return read_facts(
        con, "fact_supply_demand", scenario_id=0, measure=forecast.OUTPUT_MEASURE,
        start=demo.horizon_start, end=demo.horizon_end, keys=keys,
    )


def test_growth_of_zero_produces_the_same_plan_as_no_growth_at_all(demo):
    """The kill condition, committed in docs/forecast.md before the code.

    Not "close enough". The default must be a no-op: no re-fit, no suppression,
    no overlay. If this fails the feature does not ship, because it would mean
    installing an assumption in every plan whose owner never asked for one.
    """
    from planbrain import forecast
    from planbrain.facts.scenario import set_growth

    keys = forecast.demand_keys(demo)[:12]

    baseline_con = _fresh(demo)
    baseline = forecast.run(baseline_con, demo, keys=keys)

    zero_con = _fresh(demo)
    set_growth(zero_con, scenario_id=0, demand_growth_pct=0.0)
    zeroed = forecast.run(zero_con, demo, keys=keys)

    assert zeroed["model_mix"] == baseline["model_mix"], (
        "setting growth to zero changed which models were fitted"
    )
    assert zeroed["trends_suppressed"] == 0
    assert _forecast_rows(zero_con, demo, keys) == _forecast_rows(baseline_con, demo, keys)


def test_demand_growth_lifts_the_forecast_and_says_what_it_displaced(demo):
    """The feature doing its job, and reporting its cost in the same breath."""
    from planbrain import forecast
    from planbrain.facts.scenario import set_growth

    keys = forecast.demand_keys(demo)[:40]

    flat_con = _fresh(demo)
    forecast.run(flat_con, demo, keys=keys)
    flat = sum(f.qty for f in _forecast_rows(flat_con, demo, keys))

    grown_con = _fresh(demo)
    set_growth(grown_con, scenario_id=0, demand_growth_pct=20.0)
    report = forecast.run(grown_con, demo, keys=keys)
    grown = sum(f.qty for f in _forecast_rows(grown_con, demo, keys))

    assert grown > flat, "a 20% growth assumption did not raise the forecast"
    assert report["demand_growth_pct"] == 20.0
    assert report["growth_anchor"] == demo.history_end.isoformat()

    # The horizon is about a quarter, so a 20%/year assumption lifts the total
    # by a few percent, not by twenty. A test asserting ~20% here would be
    # asserting the wrong arithmetic and would pass only if the overlay had been
    # applied flat across the horizon instead of compounded from the anchor.
    lift = grown / flat - 1
    assert 0.01 < lift < 0.10, f"lift of {lift:.1%} is not a compounded annual 20%"

    assert report["trends_suppressed"] > 0, (
        "no fitted trend was suppressed, so either the portfolio changed or the "
        "one-source-of-trend rule is not being applied"
    )


def test_capacity_growth_moves_capacity_and_leaves_demand_alone(demo):
    """The reason these are two parameters and not one.

    If capacity growth touched the forecast, or demand growth touched available
    hours, the pair would move together and rccp would report a comfortable
    plant at every setting -- which is the answer it exists to withhold.
    """
    from planbrain import forecast, netreq, rccp
    from planbrain.facts.scenario import set_growth

    def plan(capacity_growth):
        con = _fresh(demo)
        if capacity_growth:
            set_growth(con, scenario_id=0, capacity_growth_pct=capacity_growth)
        forecast.run(con, demo)
        netreq.run(con, demo, lot_sizing="cost_based")
        return con, rccp.run(con, demo)

    flat_con, flat = plan(0.0)
    grown_con, grown = plan(25.0)

    flat_hours = sum(d["capacity_hours"] for d in flat["resources"].values())
    grown_hours = sum(d["capacity_hours"] for d in grown["resources"].values())
    assert grown_hours > flat_hours, "capacity growth did not raise available hours"

    flat_load = sum(d["load_hours"] for d in flat["resources"].values())
    grown_load = sum(d["load_hours"] for d in grown["resources"].values())
    assert grown_load == pytest.approx(flat_load), (
        "capacity growth changed the work to be done; it must only change the "
        "hours available to do it"
    )
    assert grown["capacity_growth_pct"] == 25.0


def test_demand_growth_does_not_quietly_relieve_the_plant(demo):
    """The other half of the same rule: growing demand must make the capacity
    picture worse, never better."""
    from planbrain import forecast, netreq, rccp
    from planbrain.facts.scenario import set_growth

    def plan(demand_growth):
        con = _fresh(demo)
        if demand_growth:
            set_growth(con, scenario_id=0, demand_growth_pct=demand_growth)
        forecast.run(con, demo)
        netreq.run(con, demo, lot_sizing="cost_based")
        return rccp.run(con, demo)

    flat = plan(0.0)
    grown = plan(30.0)

    flat_hours = sum(d["capacity_hours"] for d in flat["resources"].values())
    grown_hours = sum(d["capacity_hours"] for d in grown["resources"].values())
    assert grown_hours == pytest.approx(flat_hours), (
        "demand growth changed available capacity, so the two parameters are "
        "not independent"
    )
    assert grown["capacity_growth_pct"] == 0.0


# --------------------------------------------------------------------------
# the assumption travels with the scenario
# --------------------------------------------------------------------------

def test_a_copied_scenario_carries_its_growth_assumptions(demo):
    """A copy that reset growth to zero would be a different plan wearing the
    same name, and the difference would show up as a quieter forecast with
    nothing in the scenario to explain it."""
    from planbrain.facts.scenario import copy_scenario, growth_of, set_growth

    con = _fresh(demo)
    set_growth(con, scenario_id=0, demand_growth_pct=6.0, capacity_growth_pct=3.0)

    new_id = copy_scenario(con, source_scenario_id=0, name="what-if")
    assert growth_of(con, new_id) == (6.0, 3.0)


def test_a_committed_scenario_carries_its_growth_assumptions(demo):
    """Committed is the plan that authorises order release. If it did not record
    the assumption it was built on, nobody could say afterwards what was
    assumed when the orders went out."""
    from planbrain.facts.scenario import commit_scenario, growth_of, set_growth

    con = _fresh(demo)
    set_growth(con, scenario_id=0, demand_growth_pct=6.0, capacity_growth_pct=3.0)

    committed = commit_scenario(con, source_scenario_id=0, name="committed-plan")
    assert growth_of(con, committed) == (6.0, 3.0)


def test_growth_cannot_be_set_on_a_frozen_scenario(demo):
    """A snapshot whose assumptions can still change is not a snapshot."""
    from planbrain.facts.access import FrozenScenarioError
    from planbrain.facts.scenario import commit_scenario, set_growth

    con = _fresh(demo)
    committed = commit_scenario(con, source_scenario_id=0, name="frozen-plan")
    with pytest.raises(FrozenScenarioError):
        set_growth(con, scenario_id=committed, demand_growth_pct=5.0)


def test_setting_one_rate_leaves_the_other_alone(demo):
    """Two independent parameters that reset each other are one parameter with
    extra steps."""
    from planbrain.facts.scenario import growth_of, set_growth

    con = _fresh(demo)
    set_growth(con, scenario_id=0, demand_growth_pct=8.0)
    set_growth(con, scenario_id=0, capacity_growth_pct=4.0)
    assert growth_of(con, scenario_id=0) == (8.0, 4.0)


def test_the_database_refuses_a_rate_at_or_below_minus_one_hundred(demo):
    """Asserted at the storage layer as well as the call site: the engines
    refuse it, and so should anything that writes around them."""
    import sqlite3

    con = _fresh(demo)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE scenario SET demand_growth_pct = -100.0 WHERE scenario_id = 0")


# --------------------------------------------------------------------------
# the assumption has to reach the person reading the answer
# --------------------------------------------------------------------------

@pytest.mark.parametrize("flags", [
    ["--capacity-growth", "25"],
    ["--source", "forecast", "--demand-growth", "30"],
    ["--source", "forecast", "--demand-growth", "10", "--capacity-growth", "5"],
])
def test_the_capacity_report_states_its_assumptions(flags, capsys):
    """A feasibility verdict is the number most likely to be quoted onwards, so
    it must not travel without the assumptions it rests on.

    The first version of this printed nothing when only demand growth was set:
    the guard was `or`-ed on a key that rccp's report did not carry, so the
    branch was unreachable for one of the two parameters. Parametrised over both
    and over the combination, because that is the case a single example missed.
    """
    from tools.capacity_report import main

    assert main(flags) == 0
    out = capsys.readouterr().out
    assert "assumptions:" in out, f"no assumptions line for {flags}"
    assert "compounded from" in out


def test_the_capacity_report_refuses_demand_growth_it_cannot_apply():
    """The naive replay reads actuals; no assumption about the future changes
    them. Silently ignoring the flag would let a run succeed with numbers that
    look considered and an assumption that never applied."""
    from tools.capacity_report import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--demand-growth", "10"])
    assert exit_info.value.code != 0

