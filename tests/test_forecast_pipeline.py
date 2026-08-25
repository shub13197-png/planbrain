"""Forecasting against the demo dataset, through the accessors.

The metrics and classification are tested purely in test_forecast_metrics.py.
This file tests the plumbing and the seam: that forecasts land in the right
measure at the right grain, and that netreq can net against them.

Kept to a small sample deliberately -- AutoETS costs about half a second a fit,
and the full portfolio belongs in tools/backtest_report.py rather than in a
suite people run on every commit.
"""

import pytest

from planbrain import forecast, netreq
from planbrain.demo import build_demo, populate
from planbrain.facts.access import FrozenScenarioError, read_facts
from planbrain.facts.scenario import commit_scenario
from planbrain.netreq import GrossReqSourceError

SAMPLE = 8


@pytest.fixture(scope="module")
def demo():
    return build_demo(seed=7)


@pytest.fixture(scope="module")
def sample_keys(demo):
    keys = forecast.demand_keys(demo)
    step = len(keys) / SAMPLE
    return [keys[int(i * step)] for i in range(SAMPLE)]


@pytest.fixture
def seeded(con, demo):
    populate(con, demo)
    return con


@pytest.fixture
def forecasted(seeded, demo, sample_keys):
    forecast.run(seeded, demo, keys=sample_keys)
    return seeded


def _series(con, measure, key, start, end, scenario_id=0):
    return [
        r.qty for r in read_facts(
            con, "fact_supply_demand",
            scenario_id=scenario_id, measure=measure,
            start=start, end=end, keys=[key],
        )
    ]


# --------------------------------------------------------------------------
# fitting and writing
# --------------------------------------------------------------------------

def test_run_reports_the_model_mix_not_just_a_row_count(seeded, demo, sample_keys):
    """A run where a third of the portfolio fell back to naive is a different
    result, and no row count would say so."""
    report = forecast.run(seeded, demo, keys=sample_keys)
    assert report["series"] == SAMPLE
    assert report["rows_written"] > 0
    assert sum(report["pattern_mix"].values()) == SAMPLE
    assert sum(report["model_mix"].values()) == SAMPLE
    assert set(report["fallbacks"]) == {"non_finite", "model_error"}


def test_forecasts_land_in_the_forecast_measure_at_depot_grain(forecasted, demo, sample_keys):
    key = sample_keys[0]
    series = _series(forecasted, "forecast", key, demo.horizon_start, demo.horizon_end)
    assert len(series) == (demo.horizon_end - demo.horizon_start).days + 1
    assert key[1] != 1, "forecasts are fitted and stored per depot, not at the plant"


def test_no_forecast_is_negative(forecasted, demo, sample_keys):
    """ETS extrapolates freely. A negative forecast nets backwards through MRP
    and manufactures supply out of nothing."""
    for key in sample_keys:
        series = _series(forecasted, "forecast", key, demo.horizon_start, demo.horizon_end)
        assert all(q >= 0 for q in series)


def test_no_forecast_is_nan(forecasted, demo, sample_keys):
    """qty has no NOT-NaN constraint and never will; a NaN would poison every
    downstream sum in silence."""
    for key in sample_keys:
        for q in _series(forecasted, "forecast", key, demo.horizon_start, demo.horizon_end):
            assert q == q
            assert q not in (float("inf"), float("-inf"))


def test_history_is_untouched(forecasted, demo, sample_keys):
    """forecast is derived=1; demand_actual is derived=0 and must not move."""
    key = sample_keys[0]
    before = _series(forecasted, "demand_actual", key, demo.history_start, demo.history_end)
    forecast.run(forecasted, demo, keys=sample_keys)
    assert _series(forecasted, "demand_actual", key, demo.history_start, demo.history_end) == before


def test_rerunning_is_idempotent(seeded, demo, sample_keys):
    first = forecast.run(seeded, demo, keys=sample_keys)
    before = _series(seeded, "forecast", sample_keys[0], demo.horizon_start, demo.horizon_end)
    second = forecast.run(seeded, demo, keys=sample_keys)
    after = _series(seeded, "forecast", sample_keys[0], demo.horizon_start, demo.horizon_end)
    assert first == second
    assert before == after


def test_run_refuses_a_frozen_scenario(seeded, demo, sample_keys):
    committed = commit_scenario(seeded, source_scenario_id=0, name="commit")
    with pytest.raises(FrozenScenarioError):
        forecast.run(seeded, demo, scenario_id=committed, keys=sample_keys)


# --------------------------------------------------------------------------
# the seam, now closed
# --------------------------------------------------------------------------

def test_netting_against_an_unwritten_forecast_is_refused(seeded, demo):
    """Every series zero means nobody ran the forecast, not that demand is nil.

    Netting against that would produce a confident, empty plan.
    """
    with pytest.raises(GrossReqSourceError, match="run planbrain.forecast"):
        netreq.resolve_gross_req(
            seeded, scenario_id=0,
            sku_ids=[p.sku_id for p in demo.parts],
            loc_ids=[loc.loc_id for loc in demo.locations],
            horizon_start=demo.horizon_start, horizon_end=demo.horizon_end,
            source="forecast",
        )


def test_forecast_source_reconciles_bottom_up_to_the_plant(forecasted, demo, sample_keys):
    """Depot-level forecasts sum to one plant-level series per SKU."""
    demand = netreq.resolve_gross_req(
        forecasted, scenario_id=0,
        sku_ids=[p.sku_id for p in demo.parts],
        loc_ids=[loc.loc_id for loc in demo.locations],
        horizon_start=demo.horizon_start, horizon_end=demo.horizon_end,
        source="forecast",
    )
    buckets = (demo.horizon_end - demo.horizon_start).days + 1
    assert demand
    assert all(len(series) == buckets for series in demand.values())

    sku = sample_keys[0][0]
    depot_total = sum(
        sum(_series(forecasted, "forecast", key, demo.horizon_start, demo.horizon_end))
        for key in sample_keys if key[0] == sku
    )
    assert sum(demand[sku]) == pytest.approx(depot_total)


def test_netreq_runs_end_to_end_on_forecast(forecasted, demo, sample_keys):
    """The whole point of the seam: netting never learns which source it got."""
    counts = netreq.run(forecasted, demo, source="forecast")
    assert set(counts) == set(netreq.OUTPUT_MEASURES)
    assert counts["planned_order_release"] > 0


def test_both_sources_produce_a_plan_through_the_same_loop(forecasted, demo):
    replay = netreq.run(forecasted, demo, source="naive_replay")
    fitted = netreq.run(forecasted, demo, source="forecast")
    assert set(replay) == set(fitted) == set(netreq.OUTPUT_MEASURES)


# --------------------------------------------------------------------------
# the backtest
# --------------------------------------------------------------------------

def test_backtest_reports_sample_against_portfolio(seeded, demo, sample_keys):
    """A MASE over an unnamed subset is unfalsifiable."""
    report = forecast.backtest(
        seeded, demo, keys=sample_keys, horizon=28, n_windows=2, min_train=180
    )
    assert report["evaluated"] == SAMPLE
    assert report["portfolio"] > SAMPLE
    assert report["model"].n_scored + report["model"].n_unscored == SAMPLE


def test_the_seasonal_period_is_derived_from_the_calendar(seeded, demo, sample_keys):
    """Not a metrics constant. Swap the calendar and the period follows.

    The demo plant closes one day a week, so the period is weekly. A continuous
    operation has no calendar-imposed cycle and the honest baseline is one-step.
    """
    import dataclasses

    from planbrain.working_calendar import CONTINUOUS

    weekly = forecast.backtest(
        seeded, demo, keys=sample_keys[:2], horizon=28, n_windows=1, min_train=180
    )
    assert weekly["seasonal_period"] == demo.calendar.seasonal_period

    round_the_clock = dataclasses.replace(demo, calendar=CONTINUOUS)
    continuous = forecast.backtest(
        seeded, round_the_clock, keys=sample_keys[:2], horizon=28, n_windows=1,
        min_train=180,
    )
    assert continuous["seasonal_period"] == 1
    assert weekly["seasonal_period"] != continuous["seasonal_period"]


def test_backtest_scores_against_a_real_baseline(seeded, demo, sample_keys):
    """The baseline is seasonal naive, never the naive_replay placeholder."""
    report = forecast.backtest(
        seeded, demo, keys=sample_keys, horizon=28, n_windows=2, min_train=180
    )
    assert report["baseline"].n_scored > 0
    assert report["model"].n_scored > 0
    assert report["by_pattern"]
