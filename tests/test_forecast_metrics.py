"""MASE, demand classification and the backtest harness — all pure.

No statsforecast, no database. These are the parts where a wrong answer would
be invisible: an inflated scale factor or a quietly dropped series makes a
portfolio MASE look good without anything failing.
"""

import pytest

from planbrain.forecast.backtest import (
    backtest_series,
    rolling_origin_windows,
    seasonal_naive,
)
from planbrain.forecast.classify import (
    ERRATIC,
    INTERMITTENT,
    LUMPY,
    SMOOTH,
    UNUSABLE,
    classify,
    is_intermittent,
)
from planbrain.forecast.metrics import (
    UndefinedMASE,
    mase,
    naive_scale,
    summarise,
)


# --------------------------------------------------------------------------
# MASE
# --------------------------------------------------------------------------

def test_mase_hand_computed():
    """train [10,12,14,16] at m=1: naive errors are 2,2,2, so the scale is 2.

    Forecast errors are 1 and 1, so MAE is 1 and MASE is 1/2.
    """
    train = [10.0, 12.0, 14.0, 16.0]
    assert naive_scale(train, 1) == 2.0
    assert mase([18.0, 20.0], [17.0, 21.0], train, 1) == 0.5


def test_mase_below_one_beats_the_baseline():
    train = [10.0, 12.0, 14.0, 16.0]
    assert mase([18.0], [18.0], train, 1) == 0.0
    assert mase([18.0], [14.0], train, 1) == 2.0


def test_scale_ignores_the_evaluation_window_entirely():
    """The denominator must not see test data. Leaking it flatters every model."""
    train = [10.0, 12.0, 14.0, 16.0]
    calm = mase([18.0], [17.0], train, 1)

    # A wildly different test window cannot move the scale factor.
    assert mase([9999.0], [9998.0], train, 1) == calm


def test_a_perfectly_periodic_series_is_undefined_not_scored():
    """The trap this metric hides: a plant closed on Sunday.

    Six days of 5 then a zero, repeated, is perfectly periodic at m=7, so the
    seasonal naive error is exactly zero and MASE has no meaning. Returning a
    number here -- or silently skipping the series -- is how a portfolio average
    gets quietly improved.
    """
    train = [5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 0.0] * 3
    assert naive_scale(train, 7) == 0.0
    with pytest.raises(UndefinedMASE, match="perfectly periodic"):
        mase([5.0], [4.0], train, 7)


def test_the_same_series_looks_scorable_at_the_wrong_period():
    """At m=1 that series has a large scale factor made of pure calendar noise.

    Every Saturday-to-Sunday and Sunday-to-Monday step is an error of 5, so a
    one-step naive baseline is terrible and anything beats it. This is why the
    default seasonal period is 7, not 1.
    """
    train = [5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 0.0] * 3
    assert naive_scale(train, 1) > 0
    flattered = mase([5.0], [4.0], train, 1)
    assert flattered < 1.0, "a mediocre forecast scores well against a bad baseline"


def test_a_training_window_shorter_than_the_season_is_refused():
    with pytest.raises(UndefinedMASE, match="too short"):
        naive_scale([1.0, 2.0, 3.0], 7)


def test_mismatched_lengths_are_refused():
    with pytest.raises(ValueError, match="buckets"):
        mase([1.0, 2.0], [1.0], [1.0, 2.0, 3.0, 4.0], 1)


def test_summarise_reports_what_it_could_not_score():
    """A mean without the unscored count is an advertisement, not a result."""
    summary = summarise({"a": 0.5, "b": 1.5, "c": None, "d": float("nan")})
    assert summary["n_scored"] == 2
    assert summary["n_unscored"] == 2
    assert summary["mean_mase"] == 1.0
    assert summary["worse_than_naive"] == 1
    assert summary["best"] == 0.5
    assert summary["worst"] == 1.5


def test_summarise_of_nothing_does_not_invent_a_number():
    summary = summarise({"a": None})
    assert summary["mean_mase"] is None
    assert summary["n_scored"] == 0


# --------------------------------------------------------------------------
# demand classification
# --------------------------------------------------------------------------

def test_smooth_demand_is_smooth():
    profile = classify([10.0, 11.0, 10.0, 9.0, 10.0, 11.0, 10.0, 9.0] * 4)
    assert profile.pattern == SMOOTH
    assert profile.adi == 1.0


def test_intermittent_demand_is_detected():
    """Every fourth bucket, steady size: high interval, low size variation."""
    profile = classify([0.0, 0.0, 0.0, 10.0] * 8)
    assert profile.pattern == INTERMITTENT
    assert profile.adi == 4.0
    assert is_intermittent(profile.pattern)


def test_lumpy_demand_is_detected():
    """Rare and wildly variable in size: the hardest case, and Croston's territory."""
    sizes = [1.0, 90.0, 3.0, 150.0, 2.0, 200.0]
    series = []
    for size in sizes:
        series += [0.0, 0.0, 0.0, 0.0, size]
    profile = classify(series)
    assert profile.pattern == LUMPY
    assert is_intermittent(profile.pattern)


def test_erratic_demand_is_frequent_but_variable():
    profile = classify([1.0, 80.0, 2.0, 120.0, 3.0, 95.0] * 4)
    assert profile.pattern == ERRATIC
    assert not is_intermittent(profile.pattern)


def test_too_few_observations_is_unusable_rather_than_guessed():
    """Fitting a model to two points produces a number indistinguishable from
    a forecast once it is in the fact table."""
    assert classify([0.0] * 50 + [5.0, 7.0]).pattern == UNUSABLE
    assert classify([]).pattern == UNUSABLE


# --------------------------------------------------------------------------
# rolling origin
# --------------------------------------------------------------------------

def test_windows_are_contiguous_and_end_at_the_last_observation():
    windows = rolling_origin_windows(100, horizon=10, n_windows=3, min_train=10)
    assert [(w.test_start, w.test_end) for w in windows] == [(70, 80), (80, 90), (90, 100)]


def test_training_never_overlaps_its_test_window():
    for w in rolling_origin_windows(100, horizon=10, n_windows=3, min_train=10):
        assert w.train_end == w.test_start


def test_windows_below_the_training_floor_are_dropped_not_shortened():
    """A fold trained on almost nothing scores noise, and noise averages in
    indistinguishably from a result."""
    windows = rolling_origin_windows(30, horizon=10, n_windows=3, min_train=20)
    assert [(w.test_start, w.test_end) for w in windows] == [(20, 30)]


def test_no_windows_when_history_is_too_short():
    assert rolling_origin_windows(15, horizon=10, n_windows=2, min_train=20) == []


# --------------------------------------------------------------------------
# the baseline and the harness
# --------------------------------------------------------------------------

def test_seasonal_naive_repeats_the_last_season():
    train = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    assert seasonal_naive(train, 9, 7) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 1.0, 2.0]


def test_seasonal_naive_falls_back_when_history_is_shorter_than_a_season():
    assert seasonal_naive([4.0, 9.0], 3, 7) == [9.0, 9.0, 9.0]


def test_a_perfect_forecaster_scores_zero():
    series = [float(i % 5 + 1) for i in range(120)]
    result = backtest_series(
        series, lambda train, h: series[len(train) : len(train) + h],
        horizon=10, n_windows=3, min_train=30, seasonal_period=1,
    )
    assert result.mase == 0.0
    assert len(result.window_scores) == 3


def test_a_worse_forecaster_scores_higher_than_a_better_one():
    # Weekly shape plus a five-day cycle, so the series is NOT perfectly
    # periodic at m=7 and the scale factor is non-zero. A pure `i % 7` series
    # is unscorable by construction, which the metric correctly refuses.
    series = [10.0 + (i % 7) + 0.3 * (i % 5) for i in range(140)]
    good = backtest_series(
        series, seasonal_naive, horizon=7, n_windows=3, min_train=30,
    )
    bad = backtest_series(
        series, lambda train, h: [0.0] * h, horizon=7, n_windows=3, min_train=30,
    )
    assert good.mase is not None and bad.mase is not None
    assert bad.mase > good.mase


def test_a_short_forecast_is_refused():
    """A forecast one element short would shift the whole comparison."""
    with pytest.raises(ValueError, match="short forecast"):
        backtest_series(
            [float(i) for i in range(120)], lambda train, h: [0.0] * (h - 1),
            horizon=10, n_windows=2, min_train=30, seasonal_period=1,
        )


def test_an_unscorable_series_says_why():
    result = backtest_series(
        [1.0] * 20, seasonal_naive, horizon=10, n_windows=3, min_train=50,
    )
    assert result.mase is None
    assert "too short" in result.unscored_reason
