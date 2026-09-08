"""Seasons a daily forecast cannot see.

**The product compresses years of history into one number per SKU.** Ninety-six
per cent of the manufacturing portfolio and ninety-three per cent of the retail
one classify as lumpy at daily grain, which routes them to TSB, which emits a
constant rate by design -- correctly, because a series that is mostly zeros
carries no reliable day-to-day timing signal. Measured over 400 series of each,
the fitted forecast is flat for 100% of manufacturing series and 98% of retail
ones.

The structure has not gone anywhere; the bucket destroys it. The same classifier
on the same series finds a forecastable pattern in 44% of manufacturing series
and 52% of retail ones once demand is summed into four-weekly buckets.

And even where a model *can* fit a season, it is only ever handed
`WorkingCalendar.seasonal_period`, which is 7. **A yearly cycle -- a festival, a
harvest, a Christmas quarter -- cannot be represented at all.** The only annual
term in the product is `forecast/growth.py`, a rate a person types in.

These tests are the falsifiable form of that: a series whose season is invisible
at daily grain, which the aggregating forecaster must find.
"""

import statistics
from datetime import date, timedelta

import pytest

from planbrain.forecast import classify
from planbrain.forecast.aggregate import aggregate, aggregated_forecaster, choose_bucket

YEARS = 4


def seasonal_intermittent(peak_months=(10, 11, 12), peak_multiple=6.0):
    """Four years of daily demand: sparse all year, heavy in the peak quarter.

    Orders arrive on one day in eight, so at daily grain this is lumpy and no
    daily model can see the quarter. Summed into four-weekly buckets the peak is
    unmissable. Deterministic on purpose -- a failure here is a real failure
    rather than an unlucky seed.
    """
    start = date(2020, 1, 1)
    out = []
    for i in range(365 * YEARS):
        day = start + timedelta(days=i)
        if i % 8:
            out.append(0.0)
            continue
        out.append(100.0 * peak_multiple if day.month in peak_months else 100.0)
    return out


def test_the_season_is_invisible_at_daily_grain():
    """The premise. If a daily model could see this, none of the rest is
    needed."""
    assert classify(seasonal_intermittent()).pattern in ("lumpy", "intermittent")


def test_the_season_is_visible_once_demand_is_bucketed():
    """Same series, coarser bucket, and a model that fits season is reachable."""
    assert classify(aggregate(seasonal_intermittent(), 28)).pattern in ("smooth", "erratic")


@pytest.mark.parametrize("size", [7, 28])
def test_aggregate_conserves_demand(size):
    """A rollup that loses or invents units moves every number downstream."""
    series = seasonal_intermittent()
    assert sum(aggregate(series, size)) == pytest.approx(sum(series))


def test_the_bucket_is_the_smallest_one_that_reveals_structure():
    """Not the largest. A coarser bucket always looks smoother, so choosing the
    smoothest would always choose the widest and throw away resolution for
    nothing."""
    size = choose_bucket(seasonal_intermittent())
    assert size > 1
    assert classify(aggregate(seasonal_intermittent(), size)).pattern in ("smooth", "erratic")


def test_a_series_with_no_season_is_left_alone():
    """Steady daily demand is already forecastable. Aggregating it would cost
    resolution and buy nothing."""
    assert choose_bucket([100.0] * (365 * 2)) == 1


# --------------------------------------------------------------------------
# the point of the whole exercise
# --------------------------------------------------------------------------

def test_the_forecast_rises_going_into_the_peak_quarter():
    """**The claim.** Train to the end of September, forecast the ninety days
    that follow -- which are the peak -- and the forecast must exceed the year's
    average rate. A constant cannot do this, and a constant is what the product
    produces for this series today.
    """
    series = seasonal_intermittent()
    cut = 365 * (YEARS - 1) + 273          # end of September in the final year
    train = series[:cut]

    forecast = aggregated_forecaster(train, 90)

    assert sum(forecast) > statistics.fmean(train) * 90 * 1.15, (
        "the forecast for the peak quarter is no higher than the flat annual "
        "rate, so the season is still invisible"
    )


def test_the_forecast_falls_going_into_the_quiet_season():
    """The other half. A model that forecast high everywhere would pass the test
    above and be worse than a constant."""
    series = seasonal_intermittent()
    cut = 365 * (YEARS - 1) + 365          # end of December; what follows is the trough
    train = series[:cut]

    forecast = aggregated_forecaster(train, 90)

    assert sum(forecast) < statistics.fmean(train) * 90, (
        "the forecast for the quiet quarter is at or above the flat annual "
        "rate, so this is a level shift rather than a season"
    )


def test_the_forecast_is_the_right_length_non_negative_and_finite():
    """A disaggregated seasonal fit can dip below zero in a trough, and ordering
    a negative quantity is not a thing that can happen."""
    forecast = aggregated_forecaster(seasonal_intermittent()[:365 * 3], 90)
    assert len(forecast) == 90
    assert all(v >= 0 for v in forecast)
    assert all(v == v and abs(v) != float("inf") for v in forecast)
